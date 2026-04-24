"""Tests for Phase 5 FSM extensions.

Verifies:
- AC2.2: New approval transitions (provisioning_failed, request_changes).
- Existing approval transitions still work (regression guard).
- Invalid transitions still raise InvalidTransitionError.
"""

import pytest

from rac_control_plane.services.submissions.fsm import (
    InvalidTransitionError,
    SubmissionStatus,
    transition,
)


# ---------------------------------------------------------------------------
# Parametrized: new positive transitions added in Phase 5
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "current,event,expected",
    [
        # provisioning_failed keeps submission in approved (retry gate)
        (
            SubmissionStatus.approved,
            "provisioning_failed",
            SubmissionStatus.approved,
        ),
        # request_changes from awaiting_research_review → needs_assistance
        (
            SubmissionStatus.awaiting_research_review,
            "request_changes",
            SubmissionStatus.needs_assistance,
        ),
        # request_changes from awaiting_it_review → needs_assistance
        (
            SubmissionStatus.awaiting_it_review,
            "request_changes",
            SubmissionStatus.needs_assistance,
        ),
    ],
)
def test_new_phase5_transitions(current, event, expected) -> None:
    """Phase 5 transitions are registered and produce the correct next state."""
    result = transition(current, event)
    assert result == expected


# ---------------------------------------------------------------------------
# Regression: existing approval transitions still work
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "current,event,expected",
    [
        # Happy path through approval flow
        (
            SubmissionStatus.awaiting_research_review,
            "research_approved",
            SubmissionStatus.awaiting_it_review,
        ),
        (
            SubmissionStatus.awaiting_research_review,
            "research_rejected",
            SubmissionStatus.research_rejected,
        ),
        (
            SubmissionStatus.awaiting_it_review,
            "it_approved",
            SubmissionStatus.approved,
        ),
        (
            SubmissionStatus.awaiting_it_review,
            "it_rejected",
            SubmissionStatus.it_rejected,
        ),
        # provisioning_completed still transitions approved → deployed
        (
            SubmissionStatus.approved,
            "provisioning_completed",
            SubmissionStatus.deployed,
        ),
    ],
)
def test_existing_approval_transitions_unchanged(current, event, expected) -> None:
    """Existing approval transitions are unaffected by Phase 5 additions."""
    result = transition(current, event)
    assert result == expected


# ---------------------------------------------------------------------------
# Invalid transitions (negative cases)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "current,event",
    [
        # awaiting_scan cannot receive research_approved (must pass scan first)
        (SubmissionStatus.awaiting_scan, "research_approved"),
        # deployed is a terminal state
        (SubmissionStatus.deployed, "research_approved"),
        (SubmissionStatus.deployed, "provisioning_failed"),
        # provisioning_failed not valid from deployed
        (SubmissionStatus.deployed, "provisioning_completed"),
        # request_changes not valid from awaiting_scan
        (SubmissionStatus.awaiting_scan, "request_changes"),
        # request_changes not valid from approved
        (SubmissionStatus.approved, "request_changes"),
        # research/it_rejected are terminal sinks
        (SubmissionStatus.research_rejected, "research_approved"),
        (SubmissionStatus.it_rejected, "it_approved"),
    ],
)
def test_invalid_transitions_raise(current, event) -> None:
    """Invalid transitions raise InvalidTransitionError."""
    with pytest.raises(InvalidTransitionError):
        transition(current, event)


# ---------------------------------------------------------------------------
# Verify provisioning_completed still works after provisioning_failed
# (a failed + retry scenario)
# ---------------------------------------------------------------------------

def test_provisioning_retry_then_complete() -> None:
    """provisioning_failed stays approved; then provisioning_completed → deployed."""
    # First attempt fails
    state = transition(SubmissionStatus.approved, "provisioning_failed")
    assert state == SubmissionStatus.approved

    # Retry succeeds
    state = transition(state, "provisioning_completed")
    assert state == SubmissionStatus.deployed


def test_request_changes_then_resubmit() -> None:
    """request_changes → needs_assistance; user resolves → back to research review."""
    # Reviewer sends back
    state = transition(SubmissionStatus.awaiting_research_review, "request_changes")
    assert state == SubmissionStatus.needs_assistance

    # User resolves the concern
    state = transition(state, "user_resolves_action_needed")
    assert state == SubmissionStatus.awaiting_research_review
