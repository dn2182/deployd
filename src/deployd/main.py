import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.admin import router as admin_router
from .api.routes import router
from .config import get_settings
from .store.db import Store
from .worker.queue import DeployQueue

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    store = Store(settings.db_path)
    store.init()
    store.purge_old_nonces()
    app.state.store = store
    app.state.queue = DeployQueue(store)
    yield
    await app.state.queue.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="deployd", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.include_router(router)
    app.include_router(admin_router)
    return app


app = create_app()

# Run: uvicorn deployd.main:app --host 127.0.0.1 --port 8300
