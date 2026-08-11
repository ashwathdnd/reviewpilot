from __future__ import annotations

import logging
import re
from typing import Any

from app.models import Category, Finding, Severity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pattern catalog
# ---------------------------------------------------------------------------

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"][a-z0-9_\-]{4,}['\"]"),
    re.compile(r"(?i)(secret[_-]?key|secretkey)\s*[:=]\s*['\"][^'\"]{4,}['\"]"),
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{4,}['\"]"),
    re.compile(r"(?i)token\s*[:=]\s*['\"][a-z0-9_\-]{4,}['\"]"),
    re.compile(r"(?i)aws[_-]?secret[_-]?access[_-]?key\s*[:=]\s*['\"][^'\"]+['\"]"),
]

DEBUG_PATTERNS = [
    re.compile(r"\bprint\s*\("),
    re.compile(r"\bbreakpoint\s*\("),
    re.compile(r"\bpdb\.set_trace\s*\("),
    re.compile(r"\bconsole\.log\s*\("),
    re.compile(r"\bdebugger;"),
    re.compile(r"\blogger\.debug\s*\("),
]

TODO_PATTERN = re.compile(r"(?i)\b(TODO|FIXME|HACK|XXX)\b")

# SQL injection: string concatenation or interpolation in query building.
SQL_INJECTION_PATTERNS = [
    re.compile(r"""(?i)(execute|cursor\.execute|raw)\s*\(\s*(f['\"]|['\"].*\+.*['\"]|rf['\"]|fr['\"])"""),
    re.compile(r"""(?i)(?:\+\s*['\"]\s*(WHERE|SELECT|INSERT|UPDATE|DELETE|DROP|ORDER\s+BY|GROUP\s+BY)\b)"""),
    re.compile(r"""(?i)(?:\.format\s*\(\s*.*\)\s*\))\s*.*\b(?:SELECT|INSERT|UPDATE|DELETE)\b"""),
]

# XSS patterns.
XSS_PATTERNS = [
    re.compile(r"\.innerHTML\s*=\s*"),
    re.compile(r"dangerouslySetInnerHTML"),
    re.compile(r"\bdocument\.write\s*\("),
    re.compile(r"\beval\s*\(\s*.*\+"),
    re.compile(r"\.outerHTML\s*=\s*"),
    re.compile(r"\binsertAdjacentHTML\s*\("),
]

# Resource leak: open() without context manager, unclosed connections.
RESOURCE_LEAK_PATTERNS = [
    re.compile(r"\bopen\s*\(\s*[^)]+\)\s*(?!\s*as\b)"),
    re.compile(r"\.acquire\s*\(\s*\)\s*$"),
    re.compile(r"\bnew\s+(?:BufferedReader|BufferedWriter|FileReader|FileWriter|FileInputStream|FileOutputStream)\s*\("),
]

# Race condition: bare async call without await or gather, shared mutable state.
RACE_CONDITION_PATTERNS = [
    re.compile(r"(?<!await\s)(?<!await\s{2})\b[a-z_][a-z0-9_]*\s*\([^)]*\)\s*$.*#.*async", re.I),
]

# Log injection: user input in log format strings.
LOG_INJECTION_PATTERNS = [
    re.compile(r"""(?i)(?:log(?:ger)?\.(?:error|warn(?:ing)?|info|debug|critical))\s*\(\s*f['\"]"""),
    re.compile(r"""(?i)(?:log(?:ger)?\.(?:error|warn(?:ing)?|info|debug|critical))\s*\(\s*['\"].*%[sd]"""),
]

# Hardcoded URLs, IPs, ports.
HARDCODED_CONFIG_PATTERNS = [
    re.compile(r"""https?://[^\s'\"<>]{10,}"""),
    re.compile(r"""\b(?:\d{1,3}\.){3}\d{1,3}\b"""),
    re.compile(r"""\bport\s*[:=]\s*\d{2,5}"""),
]

TEST_FILE_RE = re.compile(r"(^|/)(test_.*|.*_test\.py|.*\.spec\.js|.*\.test\.js|.*\.test\.ts)$")
SOURCE_CODE_RE = re.compile(r"\.(py|js|ts|jsx|tsx|go|java|rb|php|rs|c|cpp|cs)$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        _check_secrets(findings, file_path, line_no, text)
        _check_eval(findings, file_path, line_no, text)
        _check_empty_handler(findings, file_path, line_no, text, file_obj, added_lines)
        _check_debug(findings, file_path, line_no, text)
        _check_todo(findings, file_path, line_no, text)
        _check_sqli(findings, file_path, line_no, text)
        _check_xss(findings, file_path, line_no, text)
        _check_resource_leak(findings, file_path, line_no, text)
        _check_log_injection(findings, file_path, line_no, text)
        _check_hardcoded_config(findings, file_path, line_no, text)

    return findings


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def _check_secrets(findings: list[Finding], file_path: str, line_no: int, text: str) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(_make_finding(
                title="Possible hardcoded secret or API key",
                category=Category.security,
                severity=Severity.high,
                file_path=file_path,
                line=line_no,
                explanation="Added line appears to contain a hardcoded secret, API key, or password.",
                failure_scenario="If this code is committed, credentials may be exposed in version control and abused by anyone with repository access.",
                suggestion="Move secrets to environment variables, a secrets manager, or GitHub encrypted secrets.",
                confidence=0.75,
            ))
            break


def _check_eval(findings: list[Finding], file_path: str, line_no: int, text: str) -> None:
    if re.search(r"\beval\s*\(", text):
        findings.append(_make_finding(
            title="Use of eval() on user-controlled input",
            category=Category.security,
            severity=Severity.critical,
            file_path=file_path,
            line=line_no,
            explanation="The added code uses eval(), which can execute arbitrary strings.",
            failure_scenario="An attacker can inject malicious code through the evaluated string, leading to remote code execution.",
            suggestion="Use a safe parser such as ast.literal_eval for literals, or refactor to avoid dynamic evaluation.",
            confidence=0.95,
        ))


def _check_empty_handler(
    findings: list[Finding],
    file_path: str,
    line_no: int,
    text: str,
    file_obj: Any,
    added_lines: list[tuple[int, str]],
) -> None:
    match = re.match(r"\s*except\b", text)
    if not match:
        return

    # Check if the next few lines in the block are just pass, continue, or a
    # lower-indentation line (which means an empty body).  We look forward
    # through added_lines for lines at the same or deeper indent.
    current_indent = len(match.group(0)) - len(match.group(0).lstrip())

    # If the except line itself already has a body, skip.
    after_colon_match = re.match(r"\s*except\b.*:\s*(.+)$", text)
    if after_colon_match and after_colon_match.group(1).strip():
        return

    is_empty = False
    for next_ln, next_text in added_lines:
        if next_ln <= line_no:
            continue
        next_indent = len(next_text) - len(next_text.lstrip())
        stripped = next_text.strip()

        # Blank line — keep looking.
        if not stripped:
            continue

        # Same or deeper indent: check for pass/continue/...
        if next_indent >= current_indent:
            if re.match(r"^\s*(pass|continue|\.\.\.)\s*$", next_text):
                is_empty = True
                break
            # Any real code at same indent means body exists.
            if next_indent == current_indent:
                break
            continue
        else:
            # Indent went back — empty body.
            is_empty = True
            break

    if is_empty:
        findings.append(_make_finding(
            title="Empty exception handler",
            category=Category.correctness,
            severity=Severity.medium,
            file_path=file_path,
            line=line_no,
            explanation="The added except block silently swallows the exception without logging or handling it.",
            failure_scenario="Errors are swallowed, making debugging difficult and allowing the program to continue in an invalid state.",
            suggestion="Log the exception at minimum, or re-raise it after handling the specific error condition.",
            confidence=0.85,
        ))


def _check_debug(findings: list[Finding], file_path: str, line_no: int, text: str) -> None:
    for pattern in DEBUG_PATTERNS:
        if pattern.search(text):
            findings.append(_make_finding(
                title="Debug statement left in source code",
                category=Category.maintainability,
                severity=Severity.low,
                file_path=file_path,
                line=line_no,
                explanation="The added line contains a debug print, log, or breakpoint statement.",
                failure_scenario="Debug output can leak internal state to logs or break production execution at runtime.",
                suggestion="Remove debug statements before merging or replace them with proper logging at the correct level.",
                confidence=0.9,
            ))
            break


def _check_todo(findings: list[Finding], file_path: str, line_no: int, text: str) -> None:
    if TODO_PATTERN.search(text):
        findings.append(_make_finding(
            title="New TODO or FIXME comment added",
            category=Category.maintainability,
            severity=Severity.low,
            file_path=file_path,
            line=line_no,
            explanation="A task marker was introduced in the changed code.",
            failure_scenario="Unresolved markers can hide incomplete logic and accumulate technical debt, leading to future bugs.",
            suggestion="Resolve the item before merging or create a tracked issue for follow-up.",
            confidence=0.85,
        ))


def _check_sqli(findings: list[Finding], file_path: str, line_no: int, text: str) -> None:
    for pattern in SQL_INJECTION_PATTERNS:
        if pattern.search(text):
            findings.append(_make_finding(
                title="Potential SQL injection via string interpolation",
                category=Category.security,
                severity=Severity.high,
                file_path=file_path,
                line=line_no,
                explanation="The query appears to be built with string concatenation or interpolation. If any variable originates from user input, this is injectable.",
                failure_scenario="An attacker could inject arbitrary SQL, reading, modifying, or deleting data in the database.",
                suggestion="Use parameterized queries (e.g., cursor.execute(query, params)) or an ORM with safe bindings.",
                confidence=0.72,
            ))
            break


def _check_xss(findings: list[Finding], file_path: str, line_no: int, text: str) -> None:
    for pattern in XSS_PATTERNS:
        if pattern.search(text):
            findings.append(_make_finding(
                title="Potential cross-site scripting (XSS) vector",
                category=Category.security,
                severity=Severity.high,
                file_path=file_path,
                line=line_no,
                explanation="The code sets HTML content directly from a variable, which may allow script injection if the variable contains unsanitized user input.",
                failure_scenario="An attacker injects a <script> tag that executes in other users' browsers, stealing sessions or credentials.",
                suggestion="Use textContent, DOMPurify, or a framework's safe templating. For React, avoid dangerouslySetInnerHTML without sanitization.",
                confidence=0.78,
            ))
            break


def _check_resource_leak(findings: list[Finding], file_path: str, line_no: int, text: str) -> None:
    for pattern in RESOURCE_LEAK_PATTERNS:
        if pattern.search(text):
            findings.append(_make_finding(
                title="Potential resource leak: unmanaged handle",
                category=Category.correctness,
                severity=Severity.medium,
                file_path=file_path,
                line=line_no,
                explanation="A resource (file, lock, connection) is opened without a context manager or explicit close() in a finally block.",
                failure_scenario="Under load, leaked handles exhaust the process's resource limits, causing 'Too many open files' or deadlocks.",
                suggestion="Use a context manager ('with' statement) or try/finally to guarantee cleanup.",
                confidence=0.68,
            ))
            break


def _check_log_injection(findings: list[Finding], file_path: str, line_no: int, text: str) -> None:
    for pattern in LOG_INJECTION_PATTERNS:
        if pattern.search(text):
            findings.append(_make_finding(
                title="Potential log injection via format string",
                category=Category.security,
                severity=Severity.low,
                file_path=file_path,
                line=line_no,
                explanation="A log statement uses f-string or %-formatting with a variable that could contain newlines or control characters.",
                failure_scenario="An attacker could inject fake log entries, hide malicious activity, or disrupt log parsing tools.",
                suggestion="Pass variables as structured arguments: logger.error('msg %s', var) instead of logger.error(f'msg {var}').",
                confidence=0.65,
            ))
            break


def _check_hardcoded_config(findings: list[Finding], file_path: str, line_no: int, text: str) -> None:
    for pattern in HARDCODED_CONFIG_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(_make_finding(
                title="Hardcoded configuration value",
                category=Category.maintainability,
                severity=Severity.low,
                file_path=file_path,
                line=line_no,
                explanation=f"The line contains a hardcoded configuration value: {match.group(0)[:60]}",
                failure_scenario="Hardcoded values make the code environment-specific and prevent it from running correctly in staging, CI, or production without manual changes.",
                suggestion="Extract to an environment variable, config file, or service discovery mechanism.",
                confidence=0.6,
            ))
            break


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
