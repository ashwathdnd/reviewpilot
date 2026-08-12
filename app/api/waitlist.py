from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.database import get_database
from app.models import WaitlistEntry, WaitlistRecord

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/waitlist")
async def join_waitlist(entry: WaitlistEntry):
    """Add an email to the waitlist. Idempotent — duplicate emails return 200."""
    try:
        db = get_database()
        existing = None
        with db.session() as session:
            existing = session.query(WaitlistRecord).filter_by(email=entry.email).first()
            if not existing:
                session.add(WaitlistRecord(email=entry.email))

        if existing:
            logger.info("Waitlist duplicate: %s", entry.email)
            return {"status": "ok", "detail": "Email already on waitlist."}

        logger.info("Waitlist signup: %s", entry.email)
        return {"status": "ok", "detail": "Added to waitlist."}
    except Exception as exc:
        logger.exception("Waitlist POST failed: %s", exc)
        raise


@router.get("/waitlist")
async def list_waitlist(token: str = Query(...)):
    """View waitlist entries. Requires admin token."""
    settings = get_settings()
    if not settings.admin_token or token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Invalid token")

    db = get_database()
    with db.session() as session:
        entries = session.query(WaitlistRecord).order_by(WaitlistRecord.created_at.desc()).all()

    return {
        "count": len(entries),
        "entries": [
            {"email": e.email, "joined": e.created_at.isoformat() if e.created_at else None}
            for e in entries
        ],
    }
