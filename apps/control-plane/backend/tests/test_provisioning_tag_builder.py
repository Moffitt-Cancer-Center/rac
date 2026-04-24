"""Tests for provisioning/tag_builder.py — Functional Core.

Property test: every result contains the 4 AC11.1 required tags + rac_managed_by.
"""

from uuid import UUID, uuid4

import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from rac_control_plane.provisioning.tag_builder import build_tier3_tags

# The four required AC11.1 Tier 3 tags
_AC11_REQUIRED = {"rac_env", "rac_app_slug", "rac_pi_principal_id", "rac_submission_id"}
_ALL_REQUIRED = _AC11_REQUIRED | {"rac_managed_by"}


def _sample_tags() -> dict[str, str]:
    return build_tier3_tags(
        slug="my-app",
        pi_principal_id=uuid4(),
        submission_id=uuid4(),
        env="dev",
    )


# ---------------------------------------------------------------------------
# Basic value tests
# ---------------------------------------------------------------------------

def test_returns_all_required_keys() -> None:
    tags = _sample_tags()
    assert _ALL_REQUIRED.issubset(tags.keys())


def test_rac_managed_by_is_control_plane() -> None:
    tags = _sample_tags()
    assert tags["rac_managed_by"] == "control-plane"


def test_env_is_propagated() -> None:
    tags = build_tier3_tags(
        slug="app",
        pi_principal_id=uuid4(),
        submission_id=uuid4(),
        env="prod",
    )
    assert tags["rac_env"] == "prod"


def test_slug_is_propagated() -> None:
    pi = uuid4()
    sub = uuid4()
    tags = build_tier3_tags(slug="cool-slug", pi_principal_id=pi, submission_id=sub, env="dev")
    assert tags["rac_app_slug"] == "cool-slug"


def test_pi_principal_id_is_string() -> None:
    pi = uuid4()
    tags = build_tier3_tags(slug="app", pi_principal_id=pi, submission_id=uuid4(), env="dev")
    assert tags["rac_pi_principal_id"] == str(pi)


def test_submission_id_is_string() -> None:
    sub = uuid4()
    tags = build_tier3_tags(slug="app", pi_principal_id=uuid4(), submission_id=sub, env="dev")
    assert tags["rac_submission_id"] == str(sub)


def test_all_values_are_strings() -> None:
    tags = _sample_tags()
    for k, v in tags.items():
        assert isinstance(v, str), f"Tag {k!r} has non-str value: {v!r}"


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

@given(
    slug=st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-"), min_size=1, max_size=40),
    env=st.sampled_from(["dev", "staging", "prod"]),
)
@hyp_settings(max_examples=100)
def test_property_always_contains_required_tags(slug: str, env: str) -> None:
    """Every invocation produces all AC11.1 + rac_managed_by tags."""
    pi = uuid4()
    sub = uuid4()
    tags = build_tier3_tags(slug=slug, pi_principal_id=pi, submission_id=sub, env=env)
    assert _ALL_REQUIRED.issubset(tags.keys())
    assert tags["rac_managed_by"] == "control-plane"
    assert tags["rac_env"] == env
    assert tags["rac_app_slug"] == slug
    assert tags["rac_pi_principal_id"] == str(pi)
    assert tags["rac_submission_id"] == str(sub)


@given(
    pi=st.uuids(),
    sub=st.uuids(),
)
@hyp_settings(max_examples=50)
def test_property_uuid_round_trips(pi: UUID, sub: UUID) -> None:
    """pi_principal_id and submission_id round-trip as UUID string."""
    tags = build_tier3_tags(slug="test", pi_principal_id=pi, submission_id=sub, env="dev")
    assert UUID(tags["rac_pi_principal_id"]) == pi
    assert UUID(tags["rac_submission_id"]) == sub
