"""Same-app deploys serialize; different apps run concurrently."""

import asyncio
import logging

from ..store.db import Store
from .runner import run_deploy

log = logging.getLogger("deployd.worker")


class DeployQueue:
    def __init__(self, store: Store):
        self._store = store
        self._queues: dict[str, asyncio.Queue[str]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._pending_ids: set[str] = set()

    def enqueue(self, app: str, deploy_id: str) -> None:
        if deploy_id in self._pending_ids:
            return
        if app not in self._queues:
            self._queues[app] = asyncio.Queue()
            self._tasks[app] = asyncio.create_task(self._consume(app), name=f"worker:{app}")
        self._pending_ids.add(deploy_id)
        self._queues[app].put_nowait(deploy_id)

    def recover(self, registered_apps: set[str]) -> int:
        recovered = 0
        for app, deploy_id in self._store.recover_after_restart():
            if app not in registered_apps:
                self._store.add_step(
                    deploy_id,
                    "recovery",
                    "failed",
                    output="app is no longer registered",
                )
                self._store.set_status(deploy_id, "failed", finished=True)
                continue
            self.enqueue(app, deploy_id)
            recovered += 1
        return recovered

    async def _consume(self, app: str) -> None:
        q = self._queues[app]
        while True:
            deploy_id = await q.get()
            try:
                await run_deploy(self._store, app, deploy_id)
            except Exception:
                log.exception("deploy %s for %s crashed", deploy_id, app)
                self._store.add_step(
                    deploy_id,
                    "worker",
                    "failed",
                    output="unexpected worker failure; inspect deployd service logs",
                )
                self._store.set_status(deploy_id, "failed", finished=True)
            finally:
                self._pending_ids.discard(deploy_id)
                q.task_done()

    async def shutdown(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
