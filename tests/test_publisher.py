from __future__ import annotations

from app.models import Category, Finding, RiskLevel, Severity
from app.github.publisher import build_review_body, split_findings_for_review
from app.review.validator import FindingValidator, ValidatedFinding
from app.config import get_test_settings
from app.github.diff_parser import ParsedFile, _build_line_map


def _make_finding(**kwargs):
    defaults = {
        "title": "Issue",
        "category": Category.correctness,
        "severity": Severity.medium,
        "confidence": 0.9,
        "file_path": "src/main.py",
        "line": 2,
        "explanation": "Explanation text.",
        "failure_scenario": "Failure scenario.",
        "suggestion": "Fix it.",
    }
    defaults.update(kwargs)
    return Finding(**defaults)


def _make_validated(**kwargs):
    f = _make_finding(**kwargs)
    return ValidatedFinding(**f.model_dump())


def test_build_review_body_contains_all_sections():
    finding = _make_validated()
    body = build_review_body(
        summary="This PR introduces a potential correctness issue.",
        risk_level=RiskLevel.medium,
        findings=[finding],
        suggested_tests=["Add a unit test for edge case."],
    )
    assert "ReviewPilot" in body
    assert "Risk level" in body
    assert "Summary" in body
    assert "Findings by severity" in body
    assert "Suggested tests" in body
    assert "AI" in body


def test_split_findings_for_review():
    inline = _make_validated(title="Inline", line=2)
    body_only = _make_validated(title="Body", line=None)
    body_findings, comments = split_findings_for_review([inline, body_only])
    assert len(body_findings) == 1
    assert len(comments) == 1
    assert comments[0]["path"] == "src/main.py"
    assert comments[0]["line"] == 2
    assert comments[0]["side"] == "RIGHT"


def test_build_review_body_with_severity_counts():
    findings = [
        _make_validated(severity=Severity.high),
        _make_validated(severity=Severity.high),
        _make_validated(severity=Severity.low),
    ]
    body = build_review_body("Summary", RiskLevel.high, findings, [])
    assert "**HIGH:** 2" in body
    assert "**LOW:** 1" in body


def test_inline_comment_for_valid_line():
    settings = get_test_settings()
    validator = FindingValidator(settings)
    parsed = ParsedFile(
        filename="src/main.py",
        status="modified",
        patch="@@ -1,1 +1,2 @@\n a\n+b",
        changes=1,
        additions=1,
        deletions=0,
        line_map=_build_line_map("@@ -1,1 +1,2 @@\n a\n+b"),
    )
    finding = _make_finding(line=2)
    validated = validator.validate_and_rank([finding], [parsed])
    _, comments = split_findings_for_review(validated)
    assert len(comments) == 1
    assert comments[0]["line"] == 2


def test_body_only_when_line_invalid():
    settings = get_test_settings()
    validator = FindingValidator(settings)
    parsed = ParsedFile(
        filename="src/main.py",
        status="modified",
        patch="@@ -1,1 +1,2 @@\n a\n+b",
        changes=1,
        additions=1,
        deletions=0,
        line_map=_build_line_map("@@ -1,1 +1,2 @@\n a\n+b"),
    )
    finding = _make_finding(line=999)
    validated = validator.validate_and_rank([finding], [parsed])
    assert len(validated) == 0
    _, comments = split_findings_for_review(validated)
    assert len(comments) == 0
