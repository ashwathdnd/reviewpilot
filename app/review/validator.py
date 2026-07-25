from __future__ import annotations

import logging
import re

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


# Maps title keywords to a normalized finding type key.
# Two findings sharing the same normalized type on the same file/line/category
# are considered duplicates.
_FINDING_TYPE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"secret|api[_-]?\s*key|apikey|password|token|credential", re.I), "hardcoded_secret"),
    (re.compile(r"\beval\b|code.execution|dynamic.execution", re.I), "unsafe_eval"),
    (re.compile(r"\bdebug\b|print.statement|breakpoint|console\.log", re.I), "debug_statement"),
]


class FindingValidator:
    def __init__(self, settings: Settings):
        self.settings = settings

    def validate_and_rank(
        self,
        findings: list[Finding],
        parsed_files: list[ParsedFile],
        static_finding_count: int = 0,
    ) -> list[ValidatedFinding]:
        changed_files = {pf.filename: pf for pf in parsed_files}

        accepted: list[Finding] = []
        for finding in findings:
            if finding.confidence < self.settings.confidence_threshold:
                continue
            if finding.file_path not in changed_files:
                logger.debug("Finding references unchanged file %s", finding.file_path)
                continue
            accepted.append(finding)

        accepted = self._validate_lines(accepted, changed_files)

        # Deduplicate before limits.
        accepted = self._deduplicate(accepted, static_finding_count)

        accepted.sort(key=lambda f: (SEVERITY_ORDER[f.severity], -f.confidence))

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
                logger.debug(
                    "Dropping finding %s on %s line %d: line not in right-side diff",
                    finding.title,
                    finding.file_path,
                    finding.line,
                )
        return validated

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_finding_type(title: str) -> str:
        """Map a finding title to a normalized type key."""
        for pattern, norm_type in _FINDING_TYPE_PATTERNS:
            if pattern.search(title):
                return norm_type
        return title.lower().strip()

    def _deduplicate(
        self,
        findings: list[Finding],
        static_finding_count: int = 0,
    ) -> list[Finding]:
        """Group findings by (file, line, category) then merge duplicates by
        normalized type.  Static findings (the first *static_finding_count*
        items) take precedence over LLM findings."""
        groups: dict[tuple[str, int | None, str], list[tuple[int, Finding]]] = {}
        for idx, finding in enumerate(findings):
            key = (finding.file_path, finding.line, finding.category.value)
            groups.setdefault(key, []).append((idx, finding))

        result: list[Finding] = []
        for key, group in groups.items():
            if len(group) == 1:
                result.append(group[0][1])
                continue

            static_group: list[Finding] = []
            llm_group: list[Finding] = []
            for orig_idx, f in group:
                (static_group if orig_idx < static_finding_count else llm_group).append(f)

            merged: dict[str, Finding] = {}
            for f in static_group:
                norm = self._normalize_finding_type(f.title)
                merged[norm] = f

            for f in llm_group:
                norm = self._normalize_finding_type(f.title)
                if norm in merged:
                    merged[norm] = self._merge_llm_into_static(merged[norm], f)
                else:
                    merged[norm] = f

            result.extend(merged.values())

        return result

    @staticmethod
    def _merge_llm_into_static(static: Finding, llm: Finding) -> Finding:
        """Merge suggestion / failure_scenario from LLM into a static finding
        when the LLM version is more detailed."""
        if llm.suggestion and len(llm.suggestion.strip()) > len(static.suggestion.strip()):
            static.suggestion = llm.suggestion
        if llm.failure_scenario and len(llm.failure_scenario.strip()) > len(static.failure_scenario.strip()):
            static.failure_scenario = llm.failure_scenario
        return static

    # ------------------------------------------------------------------
    # Limits
    # ------------------------------------------------------------------

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
