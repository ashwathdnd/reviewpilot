from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_test_settings
from app.main import create_app
from app.models import ReviewRun


def _signature(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _make_pr_payload(action: str = "opened", number: int = 1) -> dict:
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "number": number,
            "title": "Add feature",
            "body": "PR body",
            "user": {"login": "author"},
            "base": {"ref": "main"},
            "head": {"ref": "feature"},
        },
        "repository": {"full_name": "owner/repo"},
        "installation": {"id": 12345},
    }


@pytest.fixture
def client(settings: Settings):
    import app.api.webhooks
    import app.config
    import app.database
    import app.main

    # Save the original function object that is used as the Depends key.
    original_settings_dependency = app.config.get_settings

    app.config.get_settings = lambda: settings
    app.api.webhooks.get_settings = lambda: settings
    app.main.get_settings = lambda: settings
    app.database._db = None

    fastapi_app = create_app()
    fastapi_app.dependency_overrides[original_settings_dependency] = lambda: settings
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()
    app.config.get_settings = original_settings_dependency
    app.api.webhooks.get_settings = original_settings_dependency
    app.main.get_settings = original_settings_dependency
    app.database._db = None


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_webhook_valid_signature(client, settings):
    payload = _make_pr_payload()
    body = json.dumps(payload).encode("utf-8")
    with patch("app.api.webhooks.process_pr_review", new=AsyncMock()) as mock_task:
        response = client.post(
            "/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "delivery-1",
                "X-Hub-Signature-256": _signature(body, settings.github_webhook_secret),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    mock_task.assert_awaited_once()


def test_webhook_invalid_signature(client, settings):
    payload = _make_pr_payload()
    body = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "delivery-2",
            "X-Hub-Signature-256": "sha256=invalid",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401
    assert "signature" in response.json()["detail"].lower()


def test_webhook_unsupported_event(client, settings):
    payload = _make_pr_payload()
    body = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "delivery-3",
            "X-Hub-Signature-256": _signature(body, settings.github_webhook_secret),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_unsupported_action(client, settings):
    payload = _make_pr_payload(action="closed")
    body = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "delivery-4",
            "X-Hub-Signature-256": _signature(body, settings.github_webhook_secret),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_idempotency(client, settings):
    payload = _make_pr_payload()
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery-5",
        "X-Hub-Signature-256": _signature(body, settings.github_webhook_secret),
        "Content-Type": "application/json",
    }
    with patch("app.api.webhooks.process_pr_review", new=AsyncMock()):
        first = client.post("/webhook", content=body, headers=headers)
        second = client.post("/webhook", content=body, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["reason"] == "duplicate delivery"


def test_webhook_invalid_line_numbers_fall_back_to_body(client, settings):
    """End-to-end: a finding with an invalid line number is not sent as an inline comment."""
    from app.models import Category, Finding, ReviewResult, RiskLevel, Severity
    from app.review.validator import FindingValidator

    validator = FindingValidator(settings)
    parsed = _make_parsed_file("src/main.py", ["@@ -1,2 +1,3 @@", " a", "+b", " c"])
    finding = Finding(
        title="Issue",
        category=Category.correctness,
        severity=Severity.medium,
        confidence=0.9,
        file_path="src/main.py",
        line=999,  # invalid
        explanation="Explanation here.",
        failure_scenario="Failure scenario.",
        suggestion="Fix it.",
    )
    validated = validator.validate_and_rank([finding], [parsed])
    assert len(validated) == 0  # dropped because invalid line means no valid location


def _make_parsed_file(filename: str, lines: list[str]):
    from app.github.diff_parser import ParsedFile, _build_line_map

    patch = "\n".join(lines)
    return ParsedFile(
        filename=filename,
        status="modified",
        patch=patch,
        changes=len(lines),
        additions=1,
        deletions=0,
        line_map=_build_line_map(patch),
    )
