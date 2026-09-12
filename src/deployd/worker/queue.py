"""Same-app deploys serialize; different apps run concurrently."""

import asyncio
import logging
from dataclasses import dataclass

from ..store.db import Store
from . import runner
from .website import run_connection, run_removal

log = logging.getLogger("deployd.worker")

LOCAL_KINDS = ("activate", "connect", "remove", "cleanup")


@dataclass(frozen=True)
class Job:
    kind: str = "artifact"
    release: str | None = None


class DeployQueue:
    def __init__(self, store: Store, drain_timeout: float = 600):
        self._store = store
        self._drain_timeout = drain_timeout
        self._queues: dict[str, asyncio.Queue[str]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._jobs: dict[str, Job] = {}
        self._stopping = False

    def is_removing(self, app: str) -> bool:
        return any(
            job.kind == "remove" and self._store.get_deploy(deploy_id)["app"] == app
            for deploy_id, job in self._jobs.items()
        )

    def healthy(self) -> bool:
        return all(not task.done() for task in self._tasks.values())

    def enqueue_removal(self, app: str, deploy_id: str) -> None:
        self.enqueue(app, deploy_id, Job("remove"))

    def enqueue_connection(self, app: str, deploy_id: str) -> None:
        self.enqueue(app, deploy_id, Job("connect"))

    def enqueue_activation(self, app: str, deploy_id: str, release: str) -> None:
        self.enqueue(app, deploy_id, Job("activate", release))

    def enqueue_cleanup(self, app: str, deploy_id: str, release: str) -> None:
        self.enqueue(app, deploy_id, Job("cleanup", release))

    def enqueue(self, app: str, deploy_id: str, job: Job | None = None) -> None:
        if deploy_id in self._jobs:
            return
        if app not in self._queues:
            self._queues[app] = asyncio.Queue()
            self._spawn(app)
        self._jobs[deploy_id] = job or Job()
        self._queues[app].put_nowait(deploy_id)

    def _spawn(self, app: str) -> None:
        task = asyncio.create_task(self._consume(app), name=f"worker:{app}")
        self._tasks[app] = task
        task.add_done_callback(lambda done, app=app: self._worker_done(app, done))

    def _worker_done(self, app: str, task: asyncio.Task) -> None:
        # A consumer must never die quietly: its queue would fill with jobs nobody runs.
        if self._stopping or task.cancelled():
            return
        log.error("worker for %s died, respawning", app, exc_info=task.exception())
        self._spawn(app)

    def recover(self, registered_apps: set[str]) -> int:
        recovered = 0
        newest: dict[str, str] = {}
        for app, deploy_id, kind in self._store.recover_after_restart():
            if kind in LOCAL_KINDS:
                self._store.add_step(
                    deploy_id,
                    "recovery",
                    "failed",
                    output="local operation not resumed after restart; inspect website and current release before retrying",
                )
                self._store.set_status(deploy_id, "failed", finished=True)
                continue
            if app not in registered_apps:
                self._store.add_step(
                    deploy_id,
                    "recovery",
                    "failed",
                    output="app is no longer registered",
                )
                self._store.set_status(deploy_id, "failed", finished=True)
                continue
            newest[app] = deploy_id
            self.enqueue(app, deploy_id)
            recovered += 1
        for app, deploy_id in newest.items():
            for superseded in self._store.supersede_queued(app, deploy_id):
                log.info("deploy %s superseded by %s during recovery", superseded, deploy_id)
        return recovered

    async def _consume(self, app: str) -> None:
        q = self._queues[app]
        while True:
            deploy_id = await q.get()
            job = self._jobs.get(deploy_id, Job())
            try:
                # Cancelled or superseded rows stay in the queue until their turn comes.
                if self._store.get_status(deploy_id) != "queued":
                    continue
                if job.kind == "remove":
                    await run_removal(self._store, app, deploy_id)
                elif job.kind == "connect":
                    await run_connection(self._store, app, deploy_id)
                elif job.kind == "activate":
                    await runner.run_activation(self._store, app, deploy_id, job.release)
                elif job.kind == "cleanup":
                    await runner.run_cleanup(self._store, app, deploy_id, job.release)
                else:
                    await runner.run_deploy(self._store, app, deploy_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("deploy %s for %s crashed", deploy_id, app)
                self._record_crash(deploy_id)
            finally:
                self._jobs.pop(deploy_id, None)
                q.task_done()

    def _record_crash(self, deploy_id: str) -> None:
        try:
            self._store.add_step(
                deploy_id,
                "worker",
                "failed",
                output="unexpected worker failure; inspect deployd service logs",
            )
            self._store.set_status(deploy_id, "failed", finished=True)
        except Exception:
            log.exception("could not record worker failure for %s", deploy_id)

    async def shutdown(self) -> None:
        self._stopping = True
        for t in self._tasks.values():
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        # Cutovers that were already past the point of no return finish on their own.
        pending = await runner.drain(self._drain_timeout)
        if pending:
            log.error(
                "%s deployment(s) still running at shutdown; recovery marks them failed", pending
            )
