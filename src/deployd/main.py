import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.admin import router as admin_router
from .api.routes import router
from .config import get_app_registry, get_settings
from .store.db import InstanceLock, Store
from .worker.queue import DeployQueue

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("deployd")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    instance_lock = InstanceLock(settings.db_path)
    instance_lock.acquire()
    store = Store(settings.db_path)
    try:
        store.init()
        store.purge_old_nonces()
        app.state.store = store
        app.state.queue = DeployQueue(store)
        recovered = app.state.queue.recover(set(get_app_registry()))
        if recovered:
            log.warning("recovered %s queued deployment(s) after restart", recovered)
        try:
            yield
        finally:
            await app.state.queue.shutdown()
    finally:
        instance_lock.release()


def create_app() -> FastAPI:
    app = FastAPI(title="deployd", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.include_router(router)
    app.include_router(admin_router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.bind_host, port=settings.bind_port, workers=1)
