"""Tests for detection rule discovery.

Verifies:
- AC4.4: load_rules discovers rules without any registration list
- Two fixture rule modules in a temp package → both discovered
- Rule module missing RULE attribute → warning, other rules still load
- Two modules with same rule_id → DuplicateRuleIdError
- Regression: actual detection.rules package loads without errors
"""

import sys
import textwrap
from pathlib import Path
from uuid import uuid4

import pytest

from rac_control_plane.detection.contracts import Finding, RepoContext, Rule
from rac_control_plane.detection.discovery import DuplicateRuleIdError, load_rules


def _make_rule_module(
    package_dir: Path,
    module_name: str,
    rule_id: str,
    *,
    missing_rule: bool = False,
) -> None:
    """Write a minimal rule module into package_dir."""
    if missing_rule:
        content = textwrap.dedent(
            """\
            # Module without RULE
            def not_a_rule():
                return []
            """
        )
    else:
        content = textwrap.dedent(
            f"""\
            from rac_control_plane.detection.contracts import Finding, RepoContext, Rule

            def _evaluate(ctx: RepoContext) -> list[Finding]:
                return []

            RULE = Rule(
                rule_id={rule_id!r},
                version=1,
                default_severity="warn",
                evaluate=_evaluate,
            )
            """
        )
    (package_dir / f"{module_name}.py").write_text(content)


def _setup_tmp_package(tmp_path: Path, pkg_name: str) -> tuple[Path, str]:
    """Create a temporary Python package and insert it into sys.path."""
    pkg_dir = tmp_path / pkg_name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    # Insert the parent so `import pkg_name` works
    sys.path.insert(0, str(tmp_path))
    return pkg_dir, pkg_name


def _cleanup_tmp_package(tmp_path: Path, pkg_name: str) -> None:
    """Remove the temp package from sys.path and sys.modules."""
    try:
        sys.path.remove(str(tmp_path))
    except ValueError:
        pass
    to_remove = [k for k in sys.modules if k == pkg_name or k.startswith(f"{pkg_name}.")]
    for k in to_remove:
        del sys.modules[k]


# ---------------------------------------------------------------------------
# Test: two modules discovered
# ---------------------------------------------------------------------------

def test_load_rules_discovers_two_modules(tmp_path: Path) -> None:
    """AC4.4: Two rule modules in a temp package → both discovered."""
    unique = str(uuid4()).replace("-", "_")
    pkg_name = f"test_rules_two_{unique}"
    pkg_dir, dotted = _setup_tmp_package(tmp_path, pkg_name)

    try:
        _make_rule_module(pkg_dir, "rule_a", "test/rule_a")
        _make_rule_module(pkg_dir, "rule_b", "test/rule_b")

        rules = load_rules(dotted)

        assert len(rules) == 2
        assert "test/rule_a" in rules
        assert "test/rule_b" in rules
        # Verify both are proper Rule instances
        for rule in rules.values():
            assert isinstance(rule, Rule)
    finally:
        _cleanup_tmp_package(tmp_path, pkg_name)


# ---------------------------------------------------------------------------
# Test: missing RULE attribute
# ---------------------------------------------------------------------------

def test_load_rules_skips_missing_rule(tmp_path: Path) -> None:
    """Module without RULE → warning logged, other rules still loaded."""
    unique = str(uuid4()).replace("-", "_")
    pkg_name = f"test_rules_missing_{unique}"
    pkg_dir, dotted = _setup_tmp_package(tmp_path, pkg_name)

    try:
        _make_rule_module(pkg_dir, "good_rule", "test/good_rule")
        _make_rule_module(pkg_dir, "no_rule", "test/should_not_appear", missing_rule=True)

        rules = load_rules(dotted)

        assert len(rules) == 1
        assert "test/good_rule" in rules
        # no_rule module had no RULE, so it's not in the dict
        assert "test/should_not_appear" not in rules
    finally:
        _cleanup_tmp_package(tmp_path, pkg_name)


# ---------------------------------------------------------------------------
# Test: duplicate rule_id
# ---------------------------------------------------------------------------

def test_load_rules_raises_on_duplicate_rule_id(tmp_path: Path) -> None:
    """Two modules with same rule_id → DuplicateRuleIdError."""
    unique = str(uuid4()).replace("-", "_")
    pkg_name = f"test_rules_dup_{unique}"
    pkg_dir, dotted = _setup_tmp_package(tmp_path, pkg_name)

    try:
        _make_rule_module(pkg_dir, "rule_one", "test/duplicate")
        _make_rule_module(pkg_dir, "rule_two", "test/duplicate")

        with pytest.raises(DuplicateRuleIdError, match="duplicate"):
            load_rules(dotted)
    finally:
        _cleanup_tmp_package(tmp_path, pkg_name)


# ---------------------------------------------------------------------------
# Test: non-existent package gracefully returns empty dict
# ---------------------------------------------------------------------------

def test_load_rules_nonexistent_package_returns_empty() -> None:
    """Missing package → empty dict, no exception."""
    rules = load_rules("rac_control_plane.detection.rules.does_not_exist_xyz_999")
    assert rules == {}


# ---------------------------------------------------------------------------
# Regression: actual detection.rules package loads without errors (AC4.4)
# ---------------------------------------------------------------------------

def test_load_rules_actual_package_loads() -> None:
    """AC4.4 regression: actual detection.rules package loads without errors.

    When new rule files are added to detection/rules/ they are discovered
    automatically without modifying any registration list.
    """
    # This will load whatever rules are registered in the actual package.
    # It must not raise and must return a dict of Rule objects.
    rules = load_rules()
    assert isinstance(rules, dict)
    for rule_id, rule in rules.items():
        assert isinstance(rule, Rule), f"{rule_id!r} is not a Rule"
        assert rule.rule_id == rule_id

    # AC4.4: function signature requires no extra arguments to pick up new rules
    import inspect
    sig = inspect.signature(load_rules)
    # The default argument covers the standard rules package
    assert "package" in sig.parameters


# ---------------------------------------------------------------------------
# Regression: expected rule_ids present after Tasks 4-5
# ---------------------------------------------------------------------------

def test_load_rules_expected_rule_ids() -> None:
    """After Tasks 4-5, all 7 starter rules must be discoverable."""
    rules = load_rules()
    expected = {
        "dockerfile/inline_downloads",
        "dockerfile/missing_user",
        "dockerfile/root_user",
        "repo/huge_files_in_git",
        "repo/secrets_in_repo",
        "manifest/undeclared_assets",
        "manifest/unreachable_external",
    }
    loaded_ids = set(rules.keys())
    missing = expected - loaded_ids
    assert not missing, f"Expected rule_ids not found: {missing}"
