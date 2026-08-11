from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.middleware import (
    RateLimitMiddleware,
    RequestIDMiddleware,
    RequestLoggingMiddleware,
    register_error_handlers,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    init_db(settings)
    logger.info("ReviewPilot started on port %d", settings.port)
    yield
    logger.info("ReviewPilot shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="ReviewPilot",
        description="AI-powered GitHub pull request reviewer.",
        version="1.0.0",
        lifespan=lifespan,
    )

    # --- CORS: allow the frontend to call the backend ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "https://reviewpilot.dev",
            "https://www.reviewpilot.dev",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # --- Middleware stack ---
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestLoggingMiddleware)

    # --- Error handlers ---
    register_error_handlers(app)

    from app.api import health, webhooks

    app.include_router(health.router, tags=["health"])
    app.include_router(webhooks.router, tags=["webhooks"])

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, log_level=settings.log_level)
