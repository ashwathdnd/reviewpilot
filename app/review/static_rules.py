from __future__ import annotations

import logging
import re
from typing import Any

from app.models import Category, Finding, Severity

logger = logging.getLogger(__name__)

# Heuristic patterns for common secrets.
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"][a-z0-9_\-]{16,}['\"]"),
    re.compile(r"(?i)(secret[_-]?key|secretkey)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"(?i)token\s*[:=]\s*['\"][a-z0-9_\-]{20,}['\"]"),
    re.compile(r"(?i)aws[_-]?secret[_-]?access[_-]?key\s*[:=]\s*['\"][^'\"]+['\"]"),
]

DEBUG_PATTERNS = [
    re.compile(r"\bprint\s*\("),
    re.compile(r"\bbreakpoint\s*\("),
    re.compile(r"\bpdb\.set_trace\s*\("),
    re.compile(r"\bconsole\.log\s*\("),
    re.compile(r"\bdebugger;"),
]

TODO_PATTERN = re.compile(r"(?i)\b(TODO|FIXME)\b")

TEST_FILE_RE = re.compile(r"(^|/)(test_.*|.*_test\.py|.*\.spec\.js|.*\.test\.js|.*\.test\.ts)$")
SOURCE_CODE_RE = re.compile(r"\.(py|js|ts|jsx|tsx|go|java|rb|php|rs|c|cpp|cs)$")


def _make_finding(
    title: str,
    category: Category,
    severity: Severity,
    file_path: str,
    line: int,
    explanation: str,
    failure_scenario: str,
    suggestion: str,
    confidence: float,
) -> Finding:
    return Finding(
        title=title,
        category=category,
        severity=severity,
        confidence=confidence,
        file_path=file_path,
        line=line,
        explanation=explanation,
        failure_scenario=failure_scenario,
        suggestion=suggestion,
    )


def _check_added_lines(
    file_obj: Any, added_lines: list[tuple[int, str]]
) -> list[Finding]:
    findings: list[Finding] = []
    file_path = file_obj.filename

    for line_no, text in added_lines:
        lower = text.lower()

        # Hardcoded credentials / API keys
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(
                    _make_finding(
                        title="Possible hardcoded secret or API key",
                        category=Category.security,
                        severity=Severity.high,
                        file_path=file_path,
                        line=line_no,
                        explanation="Added line appears to contain a hardcoded secret, API key, or password.",
                        failure_scenario="If this code is committed, credentials may be exposed in version control and abused by anyone with repository access.",
                        suggestion="Move secrets to environment variables, a secrets manager, or GitHub encrypted secrets.",
                        confidence=0.75,
                    )
                )
                break  # one secret finding per line is enough

        # eval usage
        if re.search(r"\beval\s*\(", text):
            findings.append(
                _make_finding(
                    title="Use of eval() on user-controlled input",
                    category=Category.security,
                    severity=Severity.critical,
                    file_path=file_path,
                    line=line_no,
                    explanation="The added code uses eval(), which can execute arbitrary strings.",
                    failure_scenario="An attacker can inject malicious code through the evaluated string, leading to remote code execution.",
                    suggestion="Use a safe parser such as ast.literal_eval for literals, or refactor to avoid dynamic evaluation.",
                    confidence=0.95,
                )
            )

        # empty exception handlers
        if re.search(r"\bexcept\b.*:\s*$", text.strip()):
            findings.append(
                _make_finding(
                    title="Empty exception handler",
                    category=Category.correctness,
                    severity=Severity.medium,
                    file_path=file_path,
                    line=line_no,
                    explanation="The added line introduces an exception handler with no body.",
                    failure_scenario="Errors are silently swallowed, making debugging difficult and allowing the program to continue in an invalid state.",
                    suggestion="Log the exception or re-raise it after handling.",
                    confidence=0.85,
                )
            )

        # debug statements
        for pattern in DEBUG_PATTERNS:
            if pattern.search(text):
                findings.append(
                    _make_finding(
                        title="Debug statement left in source code",
                        category=Category.maintainability,
                        severity=Severity.low,
                        file_path=file_path,
                        line=line_no,
                        explanation="The added line contains a debug print or breakpoint statement.",
                        failure_scenario="Debug output can leak internal state to logs or break production execution at runtime.",
                        suggestion="Remove debug statements before merging or replace them with proper logging at the correct level.",
                        confidence=0.9,
                    )
                )
                break

        # TODO / FIXME
        if TODO_PATTERN.search(text):
            findings.append(
                _make_finding(
                    title="New TODO or FIXME comment added",
                    category=Category.maintainability,
                    severity=Severity.low,
                    file_path=file_path,
                    line=line_no,
                    explanation="A TODO or FIXME marker was introduced in the changed code.",
                    failure_scenario="Unresolved TODOs can hide incomplete logic and accumulate technical debt, leading to future bugs.",
                    suggestion="Resolve the TODO before merging or create a tracked issue for follow-up.",
                    confidence=0.85,
                )
            )

    return findings


def run_static_checks(parsed_files: list[Any]) -> list[Finding]:
    """Run deterministic checks over parsed changed files."""
    from app.github.diff_parser import get_added_lines

    findings: list[Finding] = []
    for parsed_file in parsed_files:
        try:
            added_lines = get_added_lines(parsed_file)
            findings.extend(_check_added_lines(parsed_file, added_lines))
        except Exception:
            logger.exception("Static check failed for %s", parsed_file.filename)
    return findings


def source_files_changed(parsed_files: list[Any]) -> bool:
    return any(
        SOURCE_CODE_RE.search(pf.filename) and pf.additions > 0 for pf in parsed_files
    )


def test_files_changed(parsed_files: list[Any]) -> bool:
    return any(TEST_FILE_RE.search(pf.filename) for pf in parsed_files)


def check_missing_tests(parsed_files: list[Any]) -> Finding | None:
    """Add a single testing finding if source files changed but no test files changed."""
    if not source_files_changed(parsed_files):
        return None
    if test_files_changed(parsed_files):
        return None

    first_source = next(
        (pf.filename for pf in parsed_files if SOURCE_CODE_RE.search(pf.filename)), ""
    )
    return Finding(
        title="No test files updated for source changes",
        category=Category.testing,
        severity=Severity.low,
        confidence=0.6,
        file_path=first_source or "unknown",
        line=None,
        explanation="Source files were changed but no corresponding test files were modified.",
        failure_scenario="Without tests, regressions in the changed behavior may reach production undetected.",
        suggestion="Add or update unit/integration tests that exercise the changed logic.",
    )
