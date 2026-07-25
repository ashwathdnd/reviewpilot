from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request

from app.config import Settings, get_settings
from app.database import get_database
from app.exceptions import ReviewPilotError
from app.models import ReviewRun
from app.services import extract_pr_info, verify_signature

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(None),
    x_github_delivery: str | None = Header(None),
    x_github_event: str | None = Header(None),
    settings: Settings = Depends(get_settings),
):
    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": "event type not pull_request"}

    body = await request.body()

    try:
        verify_signature(body, x_hub_signature_256, settings.github_webhook_secret)
    except ReviewPilotError as exc:
        logger.warning("Webhook rejected: %s", exc.message)
        raise HTTPException(status_code=401, detail=exc.message) from exc

    if not x_github_delivery:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Delivery header.")

    db = get_database()
    with db.session() as session:
        existing = (
            session.query(ReviewRun)
            .filter_by(delivery_id=x_github_delivery)
            .first()
        )
        if existing:
            logger.info("Duplicate webhook delivery ignored: %s", x_github_delivery)
            return {"status": "ignored", "reason": "duplicate delivery"}

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc

    pr_info = extract_pr_info(payload)
    if pr_info is None:
        return {"status": "ignored", "reason": "unsupported event or action"}

    with db.session() as session:
        run = ReviewRun(
            delivery_id=x_github_delivery,
            installation_id=pr_info["installation_id"],
            repository_full_name=pr_info["repository_full_name"],
            pr_number=pr_info["pr_number"],
            pr_title=pr_info["pr_title"],
            status="queued",
        )
        session.add(run)
        session.flush()
        run_id = run.id

    background_tasks.add_task(
        process_pr_review,
        settings,
        pr_info,
        run_id,
        x_github_delivery,
    )

    return {"status": "queued", "delivery_id": x_github_delivery}


async def process_pr_review(
    settings: Settings,
    pr_info: dict[str, Any],
    run_id: int,
    delivery_id: str,
) -> None:
    """Background task: fetch diff, run review, publish, and persist results."""
    # Import here to avoid circular imports at module load time.
    from app.github.auth import GitHubAuth
    from app.github.client import split_repo_full_name
    from app.github.diff_parser import parse_github_pr_files
    from app.github.publisher import publish_review
    from app.models import FindingRecord, ReviewRun
    from app.review.pipeline import full_pipeline

    db = get_database()
    owner, repo = split_repo_full_name(pr_info["repository_full_name"])
    pr_number = pr_info["pr_number"]

    def update_run(**kwargs):
        with db.session() as session:
            run = session.query(ReviewRun).filter_by(id=run_id).first()
            if run:
                for key, value in kwargs.items():
                    setattr(run, key, value)

    update_run(status="running")

    try:
        auth = GitHubAuth(settings)
        github_client = await auth.get_client_for_installation(pr_info["installation_id"])

        try:
            files = await github_client.list_pull_request_files(owner, repo, pr_number)
        finally:
            await github_client.__aexit__(None, None, None)

        parsed_files, skipped_files = parse_github_pr_files(
            files,
            max_files=settings.max_files,
            max_chars=settings.max_chars,
        )

        logger.info(
            "Reviewing %s/%s PR #%d: %d files parsed, %d skipped",
            owner,
            repo,
            pr_number,
            len(parsed_files),
            len(skipped_files),
        )

        validated, review_result, critic_json = await full_pipeline(
            settings, pr_info, parsed_files,
        )

        github_client = await auth.get_client_for_installation(pr_info["installation_id"])
        try:
            await publish_review(
                github_client,
                owner,
                repo,
                pr_number,
                review_result,
                validated,
            )
        finally:
            await github_client.__aexit__(None, None, None)

        with db.session() as session:
            run = session.query(ReviewRun).filter_by(id=run_id).first()
            if run:
                run.status = "completed"
                run.summary = review_result.summary
                run.risk_level = review_result.risk_level.value
                run.suggested_tests = "\n".join(review_result.suggested_tests)
                run.critic_decisions = critic_json
                for finding in validated:
                    record = FindingRecord(
                        review_run_id=run.id,
                        title=finding.title,
                        category=finding.category.value,
                        severity=finding.severity.value,
                        confidence=finding.confidence,
                        file_path=finding.file_path,
                        line=finding.line,
                        explanation=finding.explanation,
                        failure_scenario=finding.failure_scenario,
                        suggestion=finding.suggestion,
                        published_to_body=1 if finding.line is None else 0,
                    )
                    session.add(record)

        logger.info(
            "Review completed for %s/%s PR #%d: %d findings published",
            owner,
            repo,
            pr_number,
            len(validated),
        )

    except Exception as exc:
        logger.exception("Review failed for delivery %s", delivery_id)
        error_message = f"{type(exc).__name__}: {str(exc)}"
        update_run(status="failed", error_message=error_message)
