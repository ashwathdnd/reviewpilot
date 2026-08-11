from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database import get_database
from app.models import WaitlistEntry, WaitlistRecord

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/waitlist")
async def join_waitlist(
    entry: WaitlistEntry,
    ResponseClass=Response,
):
    """Add an email to the waitlist. Idempotent — duplicate emails return 200."""
    db = get_database(get_settings())
    record = WaitlistRecord(email=entry.email)

    with db.session() as session:
        existing = session.query(WaitlistRecord).filter_by(email=entry.email).first()
        if existing:
            return {"status": "ok", "detail": "Email already on waitlist."}

        try:
            session.add(record)
            session.flush()
        except IntegrityError:
            return {"status": "ok", "detail": "Email already on waitlist."}

    logger.info("Waitlist signup: %s", entry.email)
    return {"status": "ok", "detail": "Added to waitlist."}
