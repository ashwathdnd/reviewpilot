from __future__ import annotations

import json
import logging
from typing import Any

from app.config import Settings
from app.exceptions import ReviewError
from app.github.diff_parser import ParsedFile
from app.models import CriticAction, CriticResult, Finding, ReviewResult, Severity
from app.review.critic import CriticEngine
from app.review.engine import ReviewEngine
from app.review.static_rules import check_missing_tests, run_static_checks
from app.review.validator import FindingValidator

logger = logging.getLogger(__name__)


def run_static_analysis(parsed_files: list[ParsedFile]) -> list[Finding]:
    findings = run_static_checks(parsed_files)
    missing_test = check_missing_tests(parsed_files)
    if missing_test:
        findings.append(missing_test)
    return findings


def build_diff_text(parsed_files: list[ParsedFile], max_chars: int) -> str:
    return "\n".join(
        f"--- {pf.filename}\n{pf.patch}" for pf in parsed_files
    )[:max_chars]


async def run_review(
    settings: Settings,
    pr_info: dict[str, Any],
    parsed_files: list[ParsedFile],
    static_findings: list[Finding],
) -> ReviewResult:
    diff_text = build_diff_text(parsed_files, settings.max_chars)
    async with ReviewEngine(settings) as engine:
        return await engine.review(
            repository=pr_info["repository_full_name"],
            pr_title=pr_info["pr_title"],
            pr_number=pr_info["pr_number"],
            author=pr_info["author"],
            base_branch=pr_info["base_branch"],
            head_branch=pr_info["head_branch"],
            diff_text=diff_text,
            static_findings=static_findings,
        )


async def run_critic(
    settings: Settings,
    parsed_files: list[ParsedFile],
    candidate_findings: list[Finding],
) -> CriticResult | None:
    if not settings.critic_enabled:
        return None
    diff_text = build_diff_text(parsed_files, settings.max_chars)
    async with CriticEngine(settings) as critic:
        return await critic.criticize(
            diff_text=diff_text,
            candidate_findings=candidate_findings,
            parsed_files=parsed_files,
        )


def apply_critic_decisions(
    findings: list[Finding],
    critic_result: CriticResult | None,
    static_finding_count: int,
    parsed_files: list[ParsedFile],
) -> tuple[list[Finding], str | None]:
    """Apply critic decisions to the candidate findings list.

    Returns (filtered_findings, updated_summary_or_None).
    """
    if critic_result is None:
        return list(findings), None

    valid_lines: dict[str, set[int]] = {}
    for pf in parsed_files:
        valid_lines[pf.filename] = set(pf.line_map.keys())

    keep = [True] * len(findings)
    for decision in critic_result.decisions:
        idx = decision.finding_index
        if idx < 0 or idx >= len(findings):
            continue
        is_static = idx < static_finding_count

        if decision.action == CriticAction.remove:
            if is_static and not decision.strong_reason:
                logger.info("Critic: preserving static finding %d despite remove suggestion", idx)
                continue
            keep[idx] = False

        elif decision.action == CriticAction.revise:
            if is_static and not decision.strong_reason:
                logger.info("Critic: preserving static finding %d despite revise suggestion", idx)
                continue
            f = findings[idx]
            if decision.revised_title is not None:
                f.title = decision.revised_title
            if decision.revised_severity is not None:
                f.severity = decision.revised_severity
            if decision.revised_explanation is not None:
                f.explanation = decision.revised_explanation
            if decision.revised_failure_scenario is not None:
                f.failure_scenario = decision.revised_failure_scenario
            if decision.revised_suggestion is not None:
                f.suggestion = decision.revised_suggestion

        elif decision.action == CriticAction.merge:
            merge_idx = decision.merge_into_index
            if merge_idx is not None and 0 <= merge_idx < len(findings) and merge_idx != idx:
                keep[idx] = False

        # Action == keep: no-op.

    filtered = [f for i, f in enumerate(findings) if keep[i]]

    for finding in critic_result.missing_findings:
        f_line = finding.line
        f_path = finding.file_path
        if f_path not in valid_lines:
            continue
        if f_line is not None and f_line not in valid_lines.get(f_path, set()):
            logger.debug("Critic missing finding has invalid line %s:%d", f_path, f_line)
            continue
        filtered.append(finding)

    return filtered, critic_result.reviewer_summary_update


def decisions_to_json(critic_result: CriticResult | None) -> str | None:
    if critic_result is None:
        return None
    return json.dumps(
        [d.model_dump() for d in critic_result.decisions],
        default=str,
    )


async def full_pipeline(
    settings: Settings,
    pr_info: dict[str, Any],
    parsed_files: list[ParsedFile],
) -> tuple[list[Any], ReviewResult, str | None]:
    """Run the full review pipeline: static -> LLM -> critic -> validator.

    Returns (validated_findings, review_result, critic_decisions_json).
    """
    static_findings = run_static_analysis(parsed_files)
    logger.info(
        "Static analysis: %d findings for %s/%s PR #%d",
        len(static_findings),
        pr_info.get("repository_full_name", ""),
        pr_info.get("pr_number", ""),
    )

    review_result = await run_review(settings, pr_info, parsed_files, static_findings)

    candidate_findings = list(review_result.findings)
    static_count = len(static_findings)

    critic_result = await run_critic(settings, parsed_files, candidate_findings)
    critic_json = decisions_to_json(critic_result)

    if critic_result:
        logger.info(
            "Critic: %d decisions, %d missing findings",
            len(critic_result.decisions),
            len(critic_result.missing_findings),
        )

    filtered, summary_update = apply_critic_decisions(
        candidate_findings,
        critic_result,
        static_count,
        parsed_files,
    )

    if summary_update:
        review_result.summary = summary_update

    validator = FindingValidator(settings)
    validated = validator.validate_and_rank(
        filtered,
        parsed_files,
        static_finding_count=static_count,
    )

    return validated, review_result, critic_json
