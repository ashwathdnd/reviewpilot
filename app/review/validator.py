from __future__ import annotations

import logging

from app.config import Settings
from app.github.diff_parser import ParsedFile
from app.models import Category, Finding, Severity

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {
    Severity.critical: 0,
    Severity.high: 1,
    Severity.medium: 2,
    Severity.low: 3,
}


class ValidatedFinding(Finding):
    """A finding that has passed validation against the PR diff."""


class FindingValidator:
    def __init__(self, settings: Settings):
        self.settings = settings

    def validate_and_rank(
        self,
        findings: list[Finding],
        parsed_files: list[ParsedFile],
    ) -> list[ValidatedFinding]:
        changed_files = {pf.filename: pf for pf in parsed_files}

        # Filter by confidence and changed file presence.
        accepted: list[Finding] = []
        for finding in findings:
            if finding.confidence < self.settings.confidence_threshold:
                continue
            if finding.file_path not in changed_files:
                logger.debug("Finding references unchanged file %s", finding.file_path)
                continue
            accepted.append(finding)

        # Validate line numbers against the right side of the diff.
        accepted = self._validate_lines(accepted, changed_files)

        # Deduplicate.
        accepted = self._deduplicate(accepted)

        # Rank: severity first, then confidence.
        accepted.sort(key=lambda f: (SEVERITY_ORDER[f.severity], -f.confidence))

        # Apply per-file cap then global cap.
        accepted = self._apply_limits(accepted)

        return [ValidatedFinding(**f.model_dump()) for f in accepted]

    def _validate_lines(
        self, findings: list[Finding], changed_files: dict[str, ParsedFile]
    ) -> list[Finding]:
        validated: list[Finding] = []
        for finding in findings:
            parsed = changed_files[finding.file_path]
            if finding.line is None:
                validated.append(finding)
                continue

            if finding.line in parsed.line_map:
                validated.append(finding)
            else:
                # Drop invalid line number rather than guessing.
                logger.debug(
                    "Dropping finding %s on %s line %d: line not in right-side diff",
                    finding.title,
                    finding.file_path,
                    finding.line,
                )
        return validated

    def _deduplicate(self, findings: list[Finding]) -> list[Finding]:
        seen: set[tuple[str, str | None, str, str]] = set()
        deduped: list[Finding] = []
        for finding in findings:
            key = (finding.file_path, finding.line, finding.category.value, finding.title)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(finding)
        return deduped

    def _apply_limits(self, findings: list[Finding]) -> list[Finding]:
        per_file_counts: dict[str, int] = {}
        result: list[Finding] = []

        for finding in findings:
            count = per_file_counts.get(finding.file_path, 0)
            if count >= self.settings.max_findings_per_file:
                continue
            per_file_counts[finding.file_path] = count + 1
            result.append(finding)
            if len(result) >= self.settings.max_findings:
                break

        return result
