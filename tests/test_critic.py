from __future__ import annotations

import json

import pytest

from app.config import get_test_settings
from app.github.diff_parser import ParsedFile, _build_line_map
from app.models import (
    Category,
    CriticAction,
    CriticFindingDecision,
    CriticResult,
    Finding,
    Severity,
)
from app.review.pipeline import apply_critic_decisions


def _parsed(filename: str, patch: str) -> ParsedFile:
    return ParsedFile(
        filename=filename,
        status="modified",
        patch=patch,
        changes=1,
        additions=1,
        deletions=0,
        line_map=_build_line_map(patch),
    )


def _finding(**kw) -> Finding:
    d = {
        "title": "Some issue",
        "category": Category.correctness,
        "severity": Severity.medium,
        "confidence": 0.9,
        "file_path": "src/main.py",
        "line": 2,
        "explanation": "An explanation that is long enough.",
        "failure_scenario": "A failure scenario here.",
        "suggestion": "Suggestion text.",
    }
    d.update(kw)
    return Finding(**d)


# ------------------------------------------------------------------
# Duplicate removal
# ------------------------------------------------------------------

def test_critic_removes_duplicate():
    parsed = [_parsed("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")]
    findings = [
        _finding(title="Hardcoded API Key", category=Category.security, line=2),
        _finding(title="Possible secret", category=Category.security, line=2),
        _finding(title="Missing null check", category=Category.correctness, line=2),
    ]
    critic = CriticResult(
        decisions=[
            CriticFindingDecision(
                finding_index=1, action=CriticAction.remove,
                reason="Duplicate of finding 0.", strong_reason=True,
            ),
        ],
    )
    filtered, _ = apply_critic_decisions(findings, critic, 3, parsed)
    assert len(filtered) == 2
    assert filtered[0].title == "Hardcoded API Key"
    assert filtered[1].title == "Missing null check"


# ------------------------------------------------------------------
# Unsupported claims
# ------------------------------------------------------------------

def test_critic_removes_unsupported_claim():
    parsed = [_parsed("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")]
    findings = [
        _finding(title="SQL injection risk", category=Category.security, line=2),
    ]
    critic = CriticResult(
        decisions=[
            CriticFindingDecision(
                finding_index=0, action=CriticAction.remove,
                reason="No SQL in this diff.", strong_reason=True,
            ),
        ],
    )
    filtered, _ = apply_critic_decisions(findings, critic, 1, parsed)
    assert len(filtered) == 0


# ------------------------------------------------------------------
# Severity correction
# ------------------------------------------------------------------

def test_critic_revises_severity():
    parsed = [_parsed("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")]
    findings = [
        _finding(title="Minor logging issue", severity=Severity.high, line=2),
    ]
    critic = CriticResult(
        decisions=[
            CriticFindingDecision(
                finding_index=0, action=CriticAction.revise,
                revised_severity=Severity.low,
                reason="Logging issue is low severity.",
                strong_reason=True,
            ),
        ],
    )
    filtered, _ = apply_critic_decisions(findings, critic, 0, parsed)
    assert len(filtered) == 1
    assert filtered[0].severity == Severity.low


# ------------------------------------------------------------------
# Merging
# ------------------------------------------------------------------

def test_critic_merges_findings():
    parsed = [_parsed("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")]
    findings = [
        _finding(title="Hardcoded API Key", category=Category.security, line=2),
        _finding(title="Secret in source", category=Category.security, line=2),
    ]
    critic = CriticResult(
        decisions=[
            CriticFindingDecision(
                finding_index=1, action=CriticAction.merge,
                merge_into_index=0,
                reason="Same issue as finding 0.",
            ),
        ],
    )
    filtered, _ = apply_critic_decisions(findings, critic, 2, parsed)
    assert len(filtered) == 1
    assert filtered[0].title == "Hardcoded API Key"


# ------------------------------------------------------------------
# Critic failure fallback
# ------------------------------------------------------------------

def test_critic_failure_fallback():
    parsed = [_parsed("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")]
    findings = [_finding()]
    # None result = critic failure → all findings kept
    filtered, _ = apply_critic_decisions(findings, None, 1, parsed)
    assert len(filtered) == 1


# ------------------------------------------------------------------
# Static findings preserved without strong_reason
# ------------------------------------------------------------------

def test_static_finding_preserved_without_strong_reason():
    parsed = [_parsed("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")]
    findings = [_finding(title="Static rule issue")]
    critic = CriticResult(
        decisions=[
            CriticFindingDecision(
                finding_index=0, action=CriticAction.remove,
                reason="I disagree.", strong_reason=False,
            ),
        ],
    )
    filtered, _ = apply_critic_decisions(findings, critic, 1, parsed)
    assert len(filtered) == 1  # preserved because strong_reason=False


def test_static_finding_removed_with_strong_reason():
    parsed = [_parsed("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")]
    findings = [_finding(title="Static rule issue")]
    critic = CriticResult(
        decisions=[
            CriticFindingDecision(
                finding_index=0, action=CriticAction.remove,
                reason="False positive; the value is read from env.",
                strong_reason=True,
            ),
        ],
    )
    filtered, _ = apply_critic_decisions(findings, critic, 1, parsed)
    assert len(filtered) == 0


# ------------------------------------------------------------------
# Malformed critic output (invalid indices)
# ------------------------------------------------------------------

def test_critic_invalid_index_ignored():
    parsed = [_parsed("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")]
    findings = [_finding()]
    critic = CriticResult(
        decisions=[
            CriticFindingDecision(
                finding_index=999, action=CriticAction.remove,
                reason="Out of range.",
            ),
        ],
    )
    filtered, _ = apply_critic_decisions(findings, critic, 1, parsed)
    assert len(filtered) == 1


# ------------------------------------------------------------------
# Line-number tampering prevented
# ------------------------------------------------------------------

def test_critic_missing_finding_invalid_line_rejected():
    parsed = [_parsed("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")]
    findings: list[Finding] = []
    critic = CriticResult(
        decisions=[],
        missing_findings=[
            _finding(
                title="Something on invalid line",
                file_path="src/main.py",
                line=999,
            ),
        ],
    )
    filtered, _ = apply_critic_decisions(findings, critic, 0, parsed)
    assert len(filtered) == 0  # invalid line → dropped


def test_critic_missing_finding_valid_line_accepted():
    parsed = [_parsed("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")]
    findings: list[Finding] = []
    critic = CriticResult(
        decisions=[],
        missing_findings=[
            _finding(
                title="Obvious issue",
                file_path="src/main.py",
                line=2,
            ),
        ],
    )
    filtered, _ = apply_critic_decisions(findings, critic, 0, parsed)
    assert len(filtered) == 1


# ------------------------------------------------------------------
# Critic revises LLM finding fields
# ------------------------------------------------------------------

def test_critic_revises_llm_finding():
    parsed = [_parsed("src/main.py", "@@ -1,1 +1,2 @@\n a\n+b")]
    findings = [
        _finding(
            title="Bad title",
            explanation="Short text.",
            failure_scenario="Short desc.",
            suggestion="Short fix.",
        ),
    ]
    critic = CriticResult(
        decisions=[
            CriticFindingDecision(
                finding_index=0, action=CriticAction.revise,
                revised_title="Better title",
                revised_explanation="A much better and longer explanation.",
                revised_failure_scenario="A better failure scenario here.",
                revised_suggestion="A better suggestion here.",
                reason="Original was too vague.", strong_reason=True,
            ),
        ],
    )
    filtered, _ = apply_critic_decisions(findings, critic, 0, parsed)
    assert filtered[0].title == "Better title"
    assert "better" in filtered[0].explanation


# ------------------------------------------------------------------
# Pipeline: critic disabled
# ------------------------------------------------------------------

def test_critic_disabled(settings):
    settings.critic_enabled = False
    from app.review.pipeline import run_critic
    import asyncio
    result = asyncio.run(run_critic(settings, [], []))
    assert result is None
