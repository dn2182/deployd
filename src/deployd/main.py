import asyncio
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api.admin import router as admin_router
from .api.routes import router
from .config import get_app_registry, get_settings
from .store.db import InstanceLock, Store
from .worker.directory_layout import reconcile
from .worker.queue import DeployQueue
from .worker.runner import sweep_incoming

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("deployd")

MAINTENANCE_INTERVAL_SECONDS = 3600


def sd_notify(state: str) -> None:
    path = os.environ.get("NOTIFY_SOCKET")
    if not path or os.name == "nt":
        return
    if path.startswith("@"):
        path = "\0" + path[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(path)
            sock.sendall(state.encode())
    except OSError:
        log.debug("sd_notify failed", exc_info=True)


async def _watchdog() -> None:
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec or not usec.isdigit():
        return
    interval = min(int(usec) / 1_000_000 / 2, 15)
    while True:
        sd_notify("WATCHDOG=1")
        await asyncio.sleep(interval)


async def _maintenance(store: Store) -> None:
    keep_days = get_settings().history_keep_days
    while True:
        await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)
        try:
            store.purge_old_nonces()
            purged = store.purge_history(keep_days)
            if purged:
                log.info("purged %s finished deployment(s) older than %s days", purged, keep_days)
        except Exception:
            log.exception("maintenance run failed")


def _prepare_apps() -> None:
    for name, spec in get_app_registry().items():
        try:
            removed = sweep_incoming(spec)
            if removed:
                log.warning("removed %s leftover download(s) for %s", removed, name)
            if spec.release_layout == "directory":
                reconcile(spec)
        except (OSError, ValueError):
            log.exception(
                "release recovery failed for %s; inspect its directories before retrying", name
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    instance_lock = InstanceLock(settings.db_path)
    instance_lock.acquire()
    store = Store(settings.db_path)
    background: list[asyncio.Task] = []
    try:
        sd_notify("WATCHDOG=1")
        store.init()
        store.purge_old_nonces()
        app.state.store = store
        app.state.queue = DeployQueue(store, drain_timeout=settings.drain_timeout_seconds)
        await asyncio.to_thread(_prepare_apps)
        recovered = app.state.queue.recover(set(get_app_registry()))
        if recovered:
            log.warning("recovered %s queued deployment(s) after restart", recovered)
        background = [
            asyncio.create_task(_maintenance(store), name="maintenance"),
            asyncio.create_task(_watchdog(), name="watchdog"),
        ]
        sd_notify("READY=1")
        try:
            yield
        finally:
            sd_notify("STOPPING=1")
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await app.state.queue.shutdown()
    finally:
        instance_lock.release()


def create_app() -> FastAPI:
    app = FastAPI(title="deployd", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Validation errors must not reflect credentials from setup request bodies.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                    for error in exc.errors()
                ]
            },
        )

    app.include_router(router)
    app.include_router(admin_router)
    return app


app = create_app()


def run() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        from .check import main as check_main

        sys.exit(check_main(sys.argv[2:]))
    if len(sys.argv) > 1:
        print("usage: deployd [check]", file=sys.stderr)
        sys.exit(2)
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.bind_port,
        workers=1,
        timeout_graceful_shutdown=settings.drain_timeout_seconds + 30,
    )
