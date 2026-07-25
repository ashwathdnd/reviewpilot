from __future__ import annotations

import pytest

from app.github.diff_parser import ParsedFile, _build_line_map
from app.models import Category, Finding, Severity
from app.review.validator import FindingValidator
from app.config import get_test_settings


def _parsed_file(filename: str, patch: str) -> ParsedFile:
    return ParsedFile(
        filename=filename,
        status="modified",
        patch=patch,
        changes=1,
        additions=1,
        deletions=0,
        line_map=_build_line_map(patch),
    )


def _finding(**kwargs) -> Finding:
    defaults = {
        "title": "Issue title",
        "category": Category.correctness,
        "severity": Severity.medium,
        "confidence": 0.9,
        "file_path": "src/main.py",
        "line": 2,
        "explanation": "Explanation text here.",
        "failure_scenario": "Failure scenario text here.",
        "suggestion": "Fix the issue.",
    }
    defaults.update(kwargs)
    return Finding(**defaults)


# ------------------------------------------------------------------
# Finding type normalisation
# ------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Hardcoded API Key",               "hardcoded_secret"),
    ("Possible hardcoded secret",        "hardcoded_secret"),
    ("api_key exposed in source",        "hardcoded_secret"),
    ("Password in plain text",           "hardcoded_secret"),
    ("Token leaked in config",           "hardcoded_secret"),
    ("Credential in logs",               "hardcoded_secret"),
    ("Use of eval() on user input",      "unsafe_eval"),
    ("eval() on Static Input",           "unsafe_eval"),
    ("Dynamic code execution risk",      "unsafe_eval"),
    ("Debug statement left in code",     "debug_statement"),
    ("print statement found",            "debug_statement"),
    ("breakpoint in production path",    "debug_statement"),
    ("console.log in source",            "debug_statement"),
    ("Unrelated correctness issue",      "unrelated correctness issue"),
])
def test_normalize_finding_type(title: str, expected: str):
    assert FindingValidator._normalize_finding_type(title) == expected


# ------------------------------------------------------------------
# Deduplication – static + LLM duplicates
# ------------------------------------------------------------------

def test_dedup_secret_static_and_llm():
    settings = get_test_settings()
    validator = FindingValidator(settings)
    parsed = _parsed_file("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")

    static = _finding(
        title="Possible hardcoded secret or API key",
        category=Category.security,
        file_path="src/main.py",
        line=2,
    )
    llm = _finding(
        title="Hardcoded API Key",
        category=Category.security,
        file_path="src/main.py",
        line=2,
    )
    validated = validator.validate_and_rank(
        [static, llm], [parsed], static_finding_count=1,
    )
    assert len(validated) == 1
    # Static finding should be kept.
    assert "Possible hardcoded secret" in validated[0].title


def test_dedup_eval_static_and_llm():
    settings = get_test_settings()
    validator = FindingValidator(settings)
    parsed = _parsed_file("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")

    static = _finding(
        title="Use of eval() on user-controlled input",
        category=Category.security,
        file_path="src/main.py",
        line=2,
        suggestion="Avoid eval.",
        failure_scenario="Short failure.",
    )
    llm = _finding(
        title="Use of eval() on Static Input",
        category=Category.security,
        file_path="src/main.py",
        line=2,
        suggestion="Replace eval with a safe parser such as ast.literal_eval.",
        failure_scenario="A crafted string could execute arbitrary code on the server.",
    )
    validated = validator.validate_and_rank(
        [static, llm], [parsed], static_finding_count=1,
    )
    assert len(validated) == 1
    # LLM should have contributed the richer suggestion / failure_scenario.
    assert validated[0].suggestion == llm.suggestion
    assert validated[0].failure_scenario == llm.failure_scenario


def test_differently_worded_duplicate_titles():
    settings = get_test_settings()
    validator = FindingValidator(settings)
    parsed = _parsed_file("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")

    f1 = _finding(
        title="Hardcoded API Key detected",
        category=Category.security,
        file_path="src/main.py",
        line=2,
    )
    f2 = _finding(
        title="Possible hardcoded credentials",
        category=Category.security,
        file_path="src/main.py",
        line=2,
    )
    validated = validator.validate_and_rank(
        [f1, f2], [parsed], static_finding_count=2,
    )
    assert len(validated) == 1


# ------------------------------------------------------------------
# Two genuinely different issues on the same line
# ------------------------------------------------------------------

def test_different_issues_same_line_not_deduped():
    settings = get_test_settings()
    validator = FindingValidator(settings)
    parsed = _parsed_file("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")

    security_finding = _finding(
        title="Hardcoded API Key",
        category=Category.security,
        file_path="src/main.py",
        line=2,
    )
    correctness_finding = _finding(
        title="Missing null check",
        category=Category.correctness,
        file_path="src/main.py",
        line=2,
    )
    validated = validator.validate_and_rank(
        [security_finding, correctness_finding], [parsed], static_finding_count=2,
    )
    assert len(validated) == 2


# ------------------------------------------------------------------
# Deterministic finding precedence
# ------------------------------------------------------------------

def test_deterministic_precedence():
    settings = get_test_settings()
    validator = FindingValidator(settings)
    parsed = _parsed_file("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")

    llm = _finding(
        title="Hardcoded API Key",
        category=Category.security,
        file_path="src/main.py",
        line=2,
        explanation="LLM explanation",
    )
    static = _finding(
        title="Possible hardcoded secret or API key",
        category=Category.security,
        file_path="src/main.py",
        line=2,
        explanation="Static rule found a secret.",
    )
    validated = validator.validate_and_rank(
        [static, llm], [parsed], static_finding_count=1,
    )
    assert len(validated) == 1
    # Static finding's title and explanation should be kept.
    assert "Possible hardcoded secret" in validated[0].title
    assert "Static rule found" in validated[0].explanation


# ------------------------------------------------------------------
# Limits applied after deduplication
# ------------------------------------------------------------------

def test_limits_applied_after_dedup():
    settings = get_test_settings(max_findings=1, max_findings_per_file=1)
    validator = FindingValidator(settings)
    parsed = _parsed_file("src/main.py", "@@ -1,1 +1,5 @@\n a\n+b\n+c\n+d\n+e")

    # Two secret findings (should merge into one) and two other issues.
    findings = [
        _finding(
            title="Hardcoded API Key", category=Category.security,
            file_path="src/main.py", line=2,
        ),
        _finding(
            title="Possible secret", category=Category.security,
            file_path="src/main.py", line=2,
        ),
        _finding(
            title="Missing null check", category=Category.correctness,
            file_path="src/main.py", line=3,
        ),
    ]
    validated = validator.validate_and_rank(
        findings, [parsed], static_finding_count=3,
    )
    # After dedup: 2 findings (merged secret + null check).
    # After limit (max_findings=1): only 1 should remain.
    assert len(validated) == 1


def test_dedup_three_findings_same_line():
    settings = get_test_settings(max_findings=10, max_findings_per_file=10)
    validator = FindingValidator(settings)
    parsed = _parsed_file("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")

    findings = [
        _finding(
            title="Hardcoded API Key", category=Category.security,
            file_path="src/main.py", line=2,
        ),
        _finding(
            title="Possible secret key", category=Category.security,
            file_path="src/main.py", line=2,
        ),
        _finding(
            title="Debug print left in code", category=Category.maintainability,
            file_path="src/main.py", line=2,
        ),
    ]
    validated = validator.validate_and_rank(
        findings, [parsed], static_finding_count=3,
    )
    # secret + debug = 2 distinct normalized types.
    assert len(validated) == 2
