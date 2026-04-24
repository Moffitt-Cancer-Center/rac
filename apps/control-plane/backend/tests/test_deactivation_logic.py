"""Tests for deactivation_logic — Functional Core.

Verifies:
- Active PI produces no flag.
- Disabled PI produces a flag with reason='account_disabled'.
- Missing PI (None in graph_results) produces a flag with reason='not_found'.
- Property test: every None or account_enabled=False input → exactly one flag;
  every account_enabled=True input → zero flags.

Verifies: rac-v1.AC9.2
"""

from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rac_control_plane.services.ownership.deactivation_logic import (
    AppOwnership,
    FlaggedApp,
    GraphUserSnapshot,
    compute_flagged_apps,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(pi_oid: UUID | None = None) -> AppOwnership:
    pi = pi_oid or uuid4()
    return AppOwnership(app_id=uuid4(), app_slug=f"app-{pi.hex[:6]}", pi_principal_id=pi)


def _active_snapshot(oid: UUID) -> GraphUserSnapshot:
    return GraphUserSnapshot(oid=oid, account_enabled=True)


def _disabled_snapshot(oid: UUID) -> GraphUserSnapshot:
    return GraphUserSnapshot(oid=oid, account_enabled=False)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def test_active_pi_produces_no_flag() -> None:
    """An app whose PI is active in Graph is not flagged."""
    pi = uuid4()
    app = _make_app(pi_oid=pi)
    results = compute_flagged_apps([app], {pi: _active_snapshot(pi)})
    assert results == []


def test_disabled_pi_produces_account_disabled_flag() -> None:
    """An app whose PI has account_enabled=False is flagged with 'account_disabled'."""
    pi = uuid4()
    app = _make_app(pi_oid=pi)
    flags = compute_flagged_apps([app], {pi: _disabled_snapshot(pi)})
    assert len(flags) == 1
    flag = flags[0]
    assert isinstance(flag, FlaggedApp)
    assert flag.app_id == app.app_id
    assert flag.app_slug == app.app_slug
    assert flag.pi_principal_id == pi
    assert flag.reason == "account_disabled"


def test_missing_pi_produces_not_found_flag() -> None:
    """An app whose PI returns None from Graph is flagged with 'not_found'."""
    pi = uuid4()
    app = _make_app(pi_oid=pi)
    flags = compute_flagged_apps([app], {pi: None})
    assert len(flags) == 1
    flag = flags[0]
    assert flag.reason == "not_found"
    assert flag.pi_principal_id == pi


def test_pi_absent_from_results_treated_as_not_found() -> None:
    """A PI not present in graph_results at all is treated as not_found (safe default)."""
    pi = uuid4()
    app = _make_app(pi_oid=pi)
    # graph_results is empty — PI is absent
    flags = compute_flagged_apps([app], {})
    assert len(flags) == 1
    assert flags[0].reason == "not_found"


def test_multiple_apps_same_pi() -> None:
    """Multiple apps with the same PI get flagged independently."""
    pi = uuid4()
    app1 = _make_app(pi_oid=pi)
    app2 = _make_app(pi_oid=pi)
    flags = compute_flagged_apps([app1, app2], {pi: None})
    assert len(flags) == 2
    assert {f.app_id for f in flags} == {app1.app_id, app2.app_id}


def test_mixed_batch() -> None:
    """Active PI → no flag; disabled PI → flag; missing PI → flag."""
    pi_active = uuid4()
    pi_disabled = uuid4()
    pi_missing = uuid4()

    app_active = _make_app(pi_oid=pi_active)
    app_disabled = _make_app(pi_oid=pi_disabled)
    app_missing = _make_app(pi_oid=pi_missing)

    graph_results = {
        pi_active: _active_snapshot(pi_active),
        pi_disabled: _disabled_snapshot(pi_disabled),
        pi_missing: None,
    }

    flags = compute_flagged_apps(
        [app_active, app_disabled, app_missing], graph_results
    )

    assert len(flags) == 2
    reasons_by_app = {f.app_id: f.reason for f in flags}
    assert reasons_by_app[app_disabled.app_id] == "account_disabled"
    assert reasons_by_app[app_missing.app_id] == "not_found"


def test_empty_apps_returns_empty() -> None:
    """Empty app list → empty flag list."""
    assert compute_flagged_apps([], {}) == []


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

@given(
    account_enabled=st.booleans(),
)
def test_property_one_flag_per_non_active_app(account_enabled: bool) -> None:
    """Property: None or account_enabled=False → exactly one flag per app;
    account_enabled=True → zero flags per app."""
    pi = uuid4()
    app = _make_app(pi_oid=pi)

    if account_enabled:
        snapshot: GraphUserSnapshot | None = _active_snapshot(pi)
    else:
        snapshot = _disabled_snapshot(pi)

    flags = compute_flagged_apps([app], {pi: snapshot})

    if account_enabled:
        assert len(flags) == 0
    else:
        assert len(flags) == 1
        assert flags[0].reason == "account_disabled"


@given(
    n_apps=st.integers(min_value=0, max_value=20),
)
def test_property_none_graph_result_always_flagged(n_apps: int) -> None:
    """Property: every app with a None graph result produces exactly one flag."""
    apps = [_make_app() for _ in range(n_apps)]
    graph_results: dict[UUID, GraphUserSnapshot | None] = {
        app.pi_principal_id: None for app in apps
    }

    flags = compute_flagged_apps(apps, graph_results)

    # Each app → one flag; reason must be not_found
    assert len(flags) == n_apps
    for flag in flags:
        assert flag.reason == "not_found"


@given(
    n_active=st.integers(min_value=0, max_value=10),
    n_disabled=st.integers(min_value=0, max_value=10),
)
def test_property_flag_count_matches_non_active(n_active: int, n_disabled: int) -> None:
    """Property: total flags == number of apps with non-active (None or disabled) PIs."""
    active_apps = [_make_app() for _ in range(n_active)]
    disabled_apps = [_make_app() for _ in range(n_disabled)]
    all_apps = active_apps + disabled_apps

    graph_results: dict[UUID, GraphUserSnapshot | None] = {}
    for a in active_apps:
        graph_results[a.pi_principal_id] = _active_snapshot(a.pi_principal_id)
    for a in disabled_apps:
        graph_results[a.pi_principal_id] = _disabled_snapshot(a.pi_principal_id)

    flags = compute_flagged_apps(all_apps, graph_results)
    assert len(flags) == n_disabled
