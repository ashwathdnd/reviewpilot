from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.database import init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    init_db(settings)
    logger.info("ReviewPilot started")
    yield
    logger.info("ReviewPilot shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="ReviewPilot",
        description="AI-powered GitHub pull request reviewer.",
        version="1.0.0",
        lifespan=lifespan,
    )

    from app.api import health, webhooks

    app.include_router(health.router, tags=["health"])
    app.include_router(webhooks.router, tags=["webhooks"])

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, log_level=settings.log_level)
