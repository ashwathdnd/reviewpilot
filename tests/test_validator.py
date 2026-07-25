from __future__ import annotations

import pytest

from app.config import get_test_settings
from app.github.diff_parser import ParsedFile, _build_line_map
from app.models import Category, Finding, Severity
from app.review.validator import FindingValidator


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
        "title": "Issue",
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


def test_confidence_filter():
    settings = get_test_settings(confidence_threshold=0.7)
    validator = FindingValidator(settings)
    parsed = _parsed_file("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")
    findings = [
        _finding(confidence=0.9),
        _finding(confidence=0.6),
    ]
    validated = validator.validate_and_rank(findings, [parsed])
    assert len(validated) == 1
    assert validated[0].confidence == 0.9


def test_duplicate_findings_removed():
    settings = get_test_settings()
    validator = FindingValidator(settings)
    parsed = _parsed_file("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")
    findings = [
        _finding(title="Duplicate", line=2),
        _finding(title="Duplicate", line=2),
        _finding(title="Other", line=2),
    ]
    validated = validator.validate_and_rank(findings, [parsed])
    assert len(validated) == 2
    assert set(f.title for f in validated) == {"Duplicate", "Other"}


def test_max_findings_per_file():
    settings = get_test_settings(max_findings=5, max_findings_per_file=2)
    validator = FindingValidator(settings)
    parsed = _parsed_file("src/main.py", "@@ -1,1 +1,5 @@\n a\n+b\n+c\n+d\n+e")
    findings = [
        _finding(title=f"Issue {i}", line=i + 1)
        for i in range(5)
    ]
    validated = validator.validate_and_rank(findings, [parsed])
    assert len(validated) == 2
    assert all(f.file_path == "src/main.py" for f in validated)


def test_global_max_findings():
    settings = get_test_settings(max_findings=3, max_findings_per_file=3)
    validator = FindingValidator(settings)
    parsed1 = _parsed_file("src/a.py", "@@ -1,1 +1,5 @@\n a\n+b\n+c\n+d\n+e")
    parsed2 = _parsed_file("src/b.py", "@@ -1,1 +1,5 @@\n a\n+b\n+c\n+d\n+e")
    findings = [
        _finding(title=f"Issue A{i}", file_path="src/a.py", line=i + 1)
        for i in range(5)
    ] + [
        _finding(title=f"Issue B{i}", file_path="src/b.py", line=i + 1)
        for i in range(5)
    ]
    validated = validator.validate_and_rank(findings, [parsed1, parsed2])
    assert len(validated) == 3


def test_invalid_line_number_dropped():
    settings = get_test_settings()
    validator = FindingValidator(settings)
    parsed = _parsed_file("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")
    findings = [
        _finding(line=2),
        _finding(line=999),
    ]
    validated = validator.validate_and_rank(findings, [parsed])
    assert len(validated) == 1
    assert validated[0].line == 2


def test_findings_sorted_by_severity_and_confidence():
    settings = get_test_settings(max_findings_per_file=3)
    validator = FindingValidator(settings)
    parsed = _parsed_file("src/main.py", "@@ -1,1 +1,5 @@\n a\n+b\n+c\n+d\n+e")
    findings = [
        _finding(title="low", severity=Severity.low, confidence=0.9, line=2),
        _finding(title="high", severity=Severity.high, confidence=0.8, line=3),
        _finding(title="medium", severity=Severity.medium, confidence=0.9, line=4),
    ]
    validated = validator.validate_and_rank(findings, [parsed])
    assert [f.title for f in validated] == ["high", "medium", "low"]


def test_findings_for_unchanged_file_dropped():
    settings = get_test_settings()
    validator = FindingValidator(settings)
    parsed = _parsed_file("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")
    findings = [_finding(file_path="src/other.py")]
    validated = validator.validate_and_rank(findings, [parsed])
    assert len(validated) == 0
