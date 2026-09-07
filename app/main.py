"""FastAPI application factory for the Badcase workflow service."""
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api.import_routes import router as import_router
from app.api.routes import router
from app.infrastructure.container import get_repository, get_settings


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Apply forward-only migrations before accepting requests."""
    get_repository(get_settings())
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Badcase Workflow API",
        version="0.1.0.dev0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.include_router(router)
    app.include_router(import_router)
    return app


app = create_app()
