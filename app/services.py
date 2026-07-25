from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from app.config import Settings
from app.exceptions import WebhookValidationError

logger = logging.getLogger(__name__)


def verify_signature(payload: bytes, signature: str | None, secret: str) -> None:
    """Verify the X-Hub-Signature-256 HMAC SHA-256 signature."""
    if not signature:
        raise WebhookValidationError("Missing X-Hub-Signature-256 header.")

    if not signature.startswith("sha256="):
        raise WebhookValidationError("Invalid signature format.")

    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    provided = signature[7:]

    if not hmac.compare_digest(expected, provided):
        logger.warning("Webhook signature mismatch")
        raise WebhookValidationError("Webhook signature verification failed.")


def extract_pr_info(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return normalized PR info if the event should be processed, else None."""
    event = payload.get("pull_request")
    if not event:
        return None

    action = payload.get("action")
    if action not in {"opened", "synchronize", "reopened"}:
        return None

    repository = payload.get("repository", {})
    full_name = repository.get("full_name")
    if not full_name:
        return None

    installation = payload.get("installation", {})
    installation_id = installation.get("id")
    if not installation_id:
        return None

    return {
        "action": action,
        "installation_id": int(installation_id),
        "repository_full_name": full_name,
        "pr_number": int(event["number"]),
        "pr_title": event.get("title", ""),
        "pr_body": event.get("body", ""),
        "author": event.get("user", {}).get("login", "unknown"),
        "base_branch": event.get("base", {}).get("ref", ""),
        "head_branch": event.get("head", {}).get("ref", ""),
        "html_url": event.get("html_url", ""),
    }
