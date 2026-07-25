from __future__ import annotations

import pytest

from app.github.diff_parser import ParsedFile, _build_line_map
from app.models import Category
from app.review.static_rules import (
    check_missing_tests,
    run_static_checks,
)


def _make_file(filename: str, patch: str) -> ParsedFile:
    added = patch.count("\n+") if patch.startswith("@@") else 1
    return ParsedFile(
        filename=filename,
        status="modified",
        patch=patch,
        changes=added,
        additions=added,
        deletions=0,
        line_map=_build_line_map(patch),
    )


def test_detects_hardcoded_api_key():
    pf = _make_file(
        "src/config.py",
        "@@ -1,3 +1,4 @@\n import os\n\n-API_KEY = os.getenv('API_KEY')\n+API_KEY = 'sk-1234567890abcdef1234567890'\n",
    )
    findings = run_static_checks([pf])
    assert any(f.category == Category.security and "secret" in f.title.lower() for f in findings)


def test_detects_eval():
    pf = _make_file(
        "src/run.py",
        "@@ -1,2 +1,3 @@\n def run(user_input):\n+    result = eval(user_input)\n     return result\n",
    )
    findings = run_static_checks([pf])
    assert any(f.title == "Use of eval() on user-controlled input" for f in findings)


def test_detects_empty_exception_handler():
    pf = _make_file(
        "src/run.py",
        "@@ -1,3 +1,6 @@\n def run():\n     try:\n         do_work()\n+    except Exception:\n+        pass\n     return True\n",
    )
    findings = run_static_checks([pf])
    assert any(f.title == "Empty exception handler" for f in findings)


def test_detects_debug_statement():
    pf = _make_file(
        "src/run.py",
        "@@ -1,2 +1,3 @@\n def compute(x):\n+    print('debug', x)\n     return x * 2\n",
    )
    findings = run_static_checks([pf])
    assert any(f.title == "Debug statement left in source code" for f in findings)


def test_detects_todo():
    pf = _make_file(
        "src/run.py",
        "@@ -1,2 +1,3 @@\n def compute(x):\n+    # TODO: handle edge case\n     return x * 2\n",
    )
    findings = run_static_checks([pf])
    assert any(f.title == "New TODO or FIXME comment added" for f in findings)


def test_missing_tests_finding():
    source = _make_file(
        "src/feature.py",
        "@@ -1,2 +1,3 @@\n def feature(x):\n+    return x + 1\n",
    )
    finding = check_missing_tests([source])
    assert finding is not None
    assert finding.category.value == "testing"


def test_no_missing_tests_when_test_changed():
    source = _make_file(
        "src/feature.py",
        "@@ -1,2 +1,3 @@\n def feature(x):\n+    return x + 1\n",
    )
    test = _make_file(
        "tests/test_feature.py",
        "@@ -1,2 +1,3 @@\n def test_feature():\n+    assert feature(1) == 2\n",
    )
    finding = check_missing_tests([source, test])
    assert finding is None
