"""Tests for the rule evaluator (evaluate.run_all).

Verifies:
- AC4.6: duplicate findings from same rule on different lines are preserved
- Rule raising in evaluate() → synthetic warn finding; other rules continue
- Two rules each emitting one Finding → run_all returns both
- Dedup NOT performed: same rule same line → both rows kept
"""

from pathlib import Path
from uuid import uuid4

import pytest

from rac_control_plane.detection.contracts import Finding, RepoContext, RepoFile, Rule
from rac_control_plane.detection.evaluate import run_all


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_context(tmp_path: Path) -> RepoContext:
    """Minimal RepoContext for evaluation tests."""
    return RepoContext(
        repo_root=tmp_path,
        submission_id=uuid4(),
        dockerfile_path="Dockerfile",
        dockerfile_text="FROM python:3.12\n",
        files=(RepoFile(path="Dockerfile", size_bytes=20),),
        manifest=None,
        submission_metadata={},
    )


def _finding(rule_id: str, line: int = 1) -> Finding:
    return Finding(
        rule_id=rule_id,
        rule_version=1,
        severity="warn",
        title="Test finding",
        detail="Detail text",
        line_ranges=((line, line),),
    )


def _make_rule(rule_id: str, findings: list[Finding]) -> Rule:
    """Build a rule that always returns the given findings."""
    captured = list(findings)

    def evaluate(ctx: RepoContext) -> list[Finding]:
        return captured

    return Rule(
        rule_id=rule_id,
        version=1,
        default_severity="warn",
        evaluate=evaluate,
    )


def _make_raising_rule(rule_id: str) -> Rule:
    """Build a rule whose evaluate() raises a RuntimeError."""
    def evaluate(ctx: RepoContext) -> list[Finding]:
        raise RuntimeError("intentional test error")

    return Rule(
        rule_id=rule_id,
        version=1,
        default_severity="warn",
        evaluate=evaluate,
    )


# ---------------------------------------------------------------------------
# Test: two rules → two findings
# ---------------------------------------------------------------------------

def test_run_all_two_rules_two_findings(tmp_path: Path) -> None:
    """Two rules each emitting one Finding → run_all returns 2 findings."""
    ctx = _make_context(tmp_path)
    rule_a = _make_rule("test/rule_a", [_finding("test/rule_a")])
    rule_b = _make_rule("test/rule_b", [_finding("test/rule_b")])

    results = run_all([rule_a, rule_b], ctx)

    assert len(results) == 2
    rule_ids = {f.rule_id for f in results}
    assert "test/rule_a" in rule_ids
    assert "test/rule_b" in rule_ids


# ---------------------------------------------------------------------------
# Test: AC4.6 — same rule, two findings on different lines → both preserved
# ---------------------------------------------------------------------------

def test_run_all_ac46_two_findings_same_rule_preserved(tmp_path: Path) -> None:
    """AC4.6: One rule emitting two Findings on different lines → both retained."""
    ctx = _make_context(tmp_path)
    rule = _make_rule("test/multi", [
        _finding("test/multi", line=3),
        _finding("test/multi", line=7),
    ])

    results = run_all([rule], ctx)

    assert len(results) == 2
    lines = [r.line_ranges[0][0] for r in results]
    assert 3 in lines
    assert 7 in lines


# ---------------------------------------------------------------------------
# Test: rule raising → synthetic finding; other rules continue
# ---------------------------------------------------------------------------

def test_run_all_rule_exception_becomes_synthetic_finding(tmp_path: Path) -> None:
    """Rule that raises → synthetic warn finding with title='rule error' returned."""
    ctx = _make_context(tmp_path)
    good_rule = _make_rule("test/good", [_finding("test/good")])
    bad_rule = _make_raising_rule("test/bad")

    results = run_all([bad_rule, good_rule], ctx)

    # Must have 2 results: synthetic finding from bad_rule + real finding from good_rule
    assert len(results) == 2

    synthetic = next(f for f in results if f.rule_id == "test/bad")
    assert synthetic.severity == "warn"
    assert synthetic.title == "rule error"
    assert "RuntimeError" in synthetic.detail

    real_finding = next(f for f in results if f.rule_id == "test/good")
    assert real_finding.title == "Test finding"


def test_run_all_exception_does_not_abort_other_rules(tmp_path: Path) -> None:
    """Raising rule does not prevent other rules from running."""
    ctx = _make_context(tmp_path)
    rules = [
        _make_raising_rule("test/raise_1"),
        _make_rule("test/normal_1", [_finding("test/normal_1")]),
        _make_raising_rule("test/raise_2"),
        _make_rule("test/normal_2", [_finding("test/normal_2")]),
    ]

    results = run_all(rules, ctx)

    # 2 synthetic + 2 real = 4 total
    assert len(results) == 4
    normal_ids = {f.rule_id for f in results if f.title == "Test finding"}
    assert normal_ids == {"test/normal_1", "test/normal_2"}


# ---------------------------------------------------------------------------
# Test: no dedup — same rule, same line, same finding → both preserved
# ---------------------------------------------------------------------------

def test_run_all_no_dedup(tmp_path: Path) -> None:
    """Duplicate findings (same rule, same line) are preserved as-is — no dedup."""
    ctx = _make_context(tmp_path)
    f = _finding("test/dup", line=1)
    rule = _make_rule("test/dup", [f, f])  # same object twice

    results = run_all([rule], ctx)

    assert len(results) == 2
    assert all(r.rule_id == "test/dup" for r in results)


# ---------------------------------------------------------------------------
# Test: zero rules → empty results
# ---------------------------------------------------------------------------

def test_run_all_no_rules(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    results = run_all([], ctx)
    assert results == []


# ---------------------------------------------------------------------------
# Test: rule emitting zero findings → empty
# ---------------------------------------------------------------------------

def test_run_all_rule_emits_nothing(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    rule = _make_rule("test/silent", [])
    results = run_all([rule], ctx)
    assert results == []
