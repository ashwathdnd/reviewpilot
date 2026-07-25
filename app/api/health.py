from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "reviewpilot"}


@router.get("/")
async def root() -> dict:
    return {"service": "ReviewPilot", "docs": "/docs"}
