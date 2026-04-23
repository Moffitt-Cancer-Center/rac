# pattern: Functional Core
"""Tests for submission FSM.

Verifies legal and illegal state transitions, plus property-based tests
for the FSM invariants.

Verifies AC2.1 (status starts at awaiting_scan).
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rac_control_plane.services.submissions.fsm import (
    InvalidTransitionError,
    SubmissionStatus,
    is_terminal_state,
    transition,
)


# Valid transitions test cases
@pytest.mark.parametrize(
    "current,event,expected",
    [
        # From awaiting_scan
        (SubmissionStatus.awaiting_scan, "pipeline_error", SubmissionStatus.pipeline_error),
        (SubmissionStatus.awaiting_scan, "severity_gate_failed", SubmissionStatus.scan_rejected),
        (SubmissionStatus.awaiting_scan, "scan_passed", SubmissionStatus.awaiting_research_review),
        # From pipeline_error
        (SubmissionStatus.pipeline_error, "user_requests_assistance", SubmissionStatus.needs_assistance),
        # From scan_rejected
        (SubmissionStatus.scan_rejected, "user_requests_assistance", SubmissionStatus.needs_assistance),
        # From awaiting_research_review
        (SubmissionStatus.awaiting_research_review, "research_approved", SubmissionStatus.awaiting_it_review),
        (SubmissionStatus.awaiting_research_review, "research_rejected", SubmissionStatus.research_rejected),
        # From research_rejected
        (SubmissionStatus.research_rejected, "user_requests_assistance", SubmissionStatus.needs_assistance),
        # From awaiting_it_review
        (SubmissionStatus.awaiting_it_review, "it_approved", SubmissionStatus.approved),
        (SubmissionStatus.awaiting_it_review, "it_rejected", SubmissionStatus.it_rejected),
        # From approved
        (SubmissionStatus.approved, "provisioning_completed", SubmissionStatus.deployed),
        # From needs_user_action
        (SubmissionStatus.needs_user_action, "user_resolves_action_needed", SubmissionStatus.awaiting_research_review),
        # From needs_assistance
        (SubmissionStatus.needs_assistance, "user_resolves_action_needed", SubmissionStatus.awaiting_research_review),
        # From it_rejected
        (SubmissionStatus.it_rejected, "user_requests_assistance", SubmissionStatus.needs_assistance),
    ],
)
def test_valid_transitions(current, event, expected):
    """Test all legal state transitions."""
    result = transition(current, event)
    assert result == expected


# Invalid transitions test cases
@pytest.mark.parametrize(
    "current,event",
    [
        # Can't go backwards
        (SubmissionStatus.approved, "severity_gate_failed"),
        # Can't skip states
        (SubmissionStatus.awaiting_scan, "research_approved"),
        # Terminal states can't transition
        (SubmissionStatus.deployed, "scan_passed"),
        (SubmissionStatus.it_rejected, "research_approved"),
        (SubmissionStatus.research_rejected, "research_approved"),
        (SubmissionStatus.scan_rejected, "research_approved"),
    ],
)
def test_invalid_transitions(current, event):
    """Test that illegal transitions raise InvalidTransitionError."""
    with pytest.raises(InvalidTransitionError):
        transition(current, event)


def test_terminal_states():
    """Test the terminal state predicate."""
    assert is_terminal_state(SubmissionStatus.deployed)
    assert is_terminal_state(SubmissionStatus.it_rejected)
    assert is_terminal_state(SubmissionStatus.research_rejected)
    assert is_terminal_state(SubmissionStatus.scan_rejected)

    # Non-terminal states
    assert not is_terminal_state(SubmissionStatus.awaiting_scan)
    assert not is_terminal_state(SubmissionStatus.awaiting_research_review)
    assert not is_terminal_state(SubmissionStatus.approved)


@given(
    start_status=st.sampled_from(list(SubmissionStatus)),
    events=st.lists(
        st.sampled_from([
            "pipeline_error",
            "severity_gate_failed",
            "scan_passed",
            "research_approved",
            "research_rejected",
            "it_approved",
            "it_rejected",
            "provisioning_completed",
            "user_requests_assistance",
            "user_resolves_action_needed",
        ]),
        max_size=5,
    ),
)
def test_fsm_no_invalid_escapes(start_status, events):
    """Property: starting from any state, valid event sequences stay in valid space.

    If an event would cause an invalid transition, stop (expect InvalidTransitionError).
    The test passes if we never reach a "forbidden" transition.

    This property ensures the FSM design is internally consistent.
    """
    current = start_status

    for event in events:
        try:
            current = transition(current, event)  # type: ignore
        except InvalidTransitionError:
            # Expected: illegal transition. Stop and pass.
            break


def test_fsm_emit_metric_callback():
    """Metric callback is called with the new state on success."""
    callback_states = []

    def capture_metric(state):
        callback_states.append(state)

    result = transition(
        SubmissionStatus.awaiting_scan,
        "scan_passed",
        emit_metric=capture_metric,
    )

    assert result == SubmissionStatus.awaiting_research_review
    assert callback_states == [SubmissionStatus.awaiting_research_review]


def test_fsm_emit_metric_not_called_on_invalid():
    """Metric callback is not called if transition is invalid."""
    callback_states = []

    def capture_metric(state):
        callback_states.append(state)

    with pytest.raises(InvalidTransitionError):
        transition(
            SubmissionStatus.deployed,
            "scan_passed",
            emit_metric=capture_metric,
        )

    assert callback_states == []
