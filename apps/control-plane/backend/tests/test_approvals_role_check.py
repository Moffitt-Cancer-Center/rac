"""Tests for approval role-check logic.

Verifies:
- AC2.2: principal_can_approve_stage truth table across all role combinations.
- Property test: role check is pure.
"""

from typing import Literal
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rac_control_plane.auth.principal import Principal
from rac_control_plane.services.approvals.role_check import principal_can_approve_stage
from rac_control_plane.settings import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RESEARCH_ROLE = "research_approver"
_IT_ROLE = "it_approver"


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        env="dev",
        institution_name="Test",
        parent_domain="test.local",
        brand_logo_url="https://example.com/logo.png",
        idp_tenant_id="tenant",
        idp_client_id="client",
        idp_api_client_id="api-client",
        pg_host="localhost",
        pg_db="testdb",
        pg_user="user",
        pg_password="pass",
        kv_uri="https://test-kv.vault.azure.net/",
        blob_account_url="https://test.blob.core.windows.net/",
        acr_login_server="test.azurecr.io",
        aca_env_resource_id=(
            "/subscriptions/sub/resourceGroups/rg"
            "/providers/Microsoft.App/managedEnvironments/env"
        ),
        scan_severity_gate="high",
        approver_role_research=_RESEARCH_ROLE,
        approver_role_it=_IT_ROLE,
    )


def _principal(roles: frozenset[str]) -> Principal:
    return Principal(oid=uuid4(), kind="user", roles=roles)


# ---------------------------------------------------------------------------
# Truth table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "roles,stage,expected",
    [
        # Research role only
        (frozenset([_RESEARCH_ROLE]), "research", True),
        (frozenset([_RESEARCH_ROLE]), "it", False),
        # IT role only
        (frozenset([_IT_ROLE]), "research", False),
        (frozenset([_IT_ROLE]), "it", True),
        # Both roles
        (frozenset([_RESEARCH_ROLE, _IT_ROLE]), "research", True),
        (frozenset([_RESEARCH_ROLE, _IT_ROLE]), "it", True),
        # No roles
        (frozenset(), "research", False),
        (frozenset(), "it", False),
        # Unrelated role
        (frozenset(["admin"]), "research", False),
        (frozenset(["admin"]), "it", False),
    ],
)
def test_principal_can_approve_stage_truth_table(
    roles: frozenset[str],
    stage: Literal["research", "it"],
    expected: bool,
) -> None:
    """principal_can_approve_stage returns correct bool for all role/stage combos."""
    principal = _principal(roles)
    settings = _settings()
    result = principal_can_approve_stage(principal, stage, settings=settings)
    assert result == expected


# ---------------------------------------------------------------------------
# Property tests — purity
# ---------------------------------------------------------------------------

@given(
    has_research=st.booleans(),
    has_it=st.booleans(),
    stage=st.sampled_from(["research", "it"]),
)
def test_role_check_is_pure(
    has_research: bool,
    has_it: bool,
    stage: Literal["research", "it"],
) -> None:
    """Property: principal_can_approve_stage is pure — same input → same output."""
    roles: frozenset[str] = frozenset(
        ([_RESEARCH_ROLE] if has_research else [])
        + ([_IT_ROLE] if has_it else [])
    )
    principal = _principal(roles)
    settings = _settings()
    result1 = principal_can_approve_stage(principal, stage, settings=settings)
    result2 = principal_can_approve_stage(principal, stage, settings=settings)
    assert result1 == result2


@given(st.frozensets(st.text(min_size=1, max_size=30), max_size=5))
def test_principal_without_required_role_cannot_approve(extra_roles: frozenset[str]) -> None:
    """Property: a principal with only arbitrary extra roles cannot approve either stage."""
    # Remove the known approver roles from generated roles to ensure they're not present
    roles = extra_roles - {_RESEARCH_ROLE, _IT_ROLE}
    principal = _principal(roles)
    settings = _settings()

    # Neither stage can be approved without the specific role
    if _RESEARCH_ROLE not in roles:
        assert not principal_can_approve_stage(principal, "research", settings=settings)
    if _IT_ROLE not in roles:
        assert not principal_can_approve_stage(principal, "it", settings=settings)
