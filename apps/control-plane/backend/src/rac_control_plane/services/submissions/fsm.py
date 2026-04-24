# pattern: Functional Core
# ruff: noqa: E501  -- transition-table values span >100 chars; keeping one-line-per-transition preserves readability
"""Submission Finite State Machine.

Pure functions for validating and executing state transitions.
All logic is side-effect-free; callers handle DB writes and metrics.
"""

from collections.abc import Callable
from enum import StrEnum
from typing import Literal

from rac_control_plane.errors import ApiError


class SubmissionStatus(StrEnum):
    """FSM states for submission lifecycle."""
    awaiting_scan = "awaiting_scan"
    pipeline_error = "pipeline_error"
    scan_rejected = "scan_rejected"
    needs_user_action = "needs_user_action"
    needs_assistance = "needs_assistance"
    awaiting_research_review = "awaiting_research_review"
    research_rejected = "research_rejected"
    awaiting_it_review = "awaiting_it_review"
    it_rejected = "it_rejected"
    approved = "approved"
    deployed = "deployed"


# Discriminated union of events (as Literal types)
TransitionEvent = (
    Literal["pipeline_error"]
    | Literal["severity_gate_failed"]
    | Literal["scan_passed"]
    | Literal["research_approved"]
    | Literal["research_rejected"]
    | Literal["it_approved"]
    | Literal["it_rejected"]
    | Literal["provisioning_completed"]
    | Literal["user_requests_assistance"]
    | Literal["user_resolves_action_needed"]
    | Literal["detection_needs_user_action"]
    | Literal["detection_resolved"]
)


class InvalidTransitionError(ApiError):
    """Raised when an invalid FSM transition is attempted."""

    __slots__ = ("current", "event")

    def __init__(self, current: SubmissionStatus, event: TransitionEvent) -> None:
        super().__init__(
            code="invalid_transition",
            http_status=400,
            public_message=f"Cannot transition from {current} via {event}",
        )
        # Use object.__setattr__ because ApiError.__setattr__ guards application fields
        object.__setattr__(self, "current", current)
        object.__setattr__(self, "event", event)


# State transition table: maps (current_state, event) -> new_state
_TRANSITION_TABLE: dict[tuple[SubmissionStatus, TransitionEvent], SubmissionStatus] = {
    # From awaiting_scan
    (SubmissionStatus.awaiting_scan, "pipeline_error"): SubmissionStatus.pipeline_error,
    (SubmissionStatus.awaiting_scan, "severity_gate_failed"): SubmissionStatus.scan_rejected,
    (SubmissionStatus.awaiting_scan, "scan_passed"): SubmissionStatus.awaiting_research_review,
    # From pipeline_error (can retry or request assistance)
    (SubmissionStatus.pipeline_error, "user_requests_assistance"): SubmissionStatus.needs_assistance,
    # From scan_rejected
    (SubmissionStatus.scan_rejected, "user_requests_assistance"): SubmissionStatus.needs_assistance,
    # From awaiting_research_review
    (SubmissionStatus.awaiting_research_review, "research_approved"): SubmissionStatus.awaiting_it_review,
    (SubmissionStatus.awaiting_research_review, "research_rejected"): SubmissionStatus.research_rejected,
    # From research_rejected
    (SubmissionStatus.research_rejected, "user_requests_assistance"): SubmissionStatus.needs_assistance,
    # From awaiting_it_review
    (SubmissionStatus.awaiting_it_review, "it_approved"): SubmissionStatus.approved,
    (SubmissionStatus.awaiting_it_review, "it_rejected"): SubmissionStatus.it_rejected,
    # From approved
    (SubmissionStatus.approved, "provisioning_completed"): SubmissionStatus.deployed,
    # From needs_user_action
    (SubmissionStatus.needs_user_action, "user_resolves_action_needed"): SubmissionStatus.awaiting_research_review,
    # Detection-engine transitions (Phase 4)
    # awaiting_scan → needs_user_action when detection finds issues requiring user action
    (SubmissionStatus.awaiting_scan, "detection_needs_user_action"): SubmissionStatus.needs_user_action,
    # needs_user_action → awaiting_scan when all error findings have been decided
    (SubmissionStatus.needs_user_action, "detection_resolved"): SubmissionStatus.awaiting_scan,
    # From needs_assistance
    (SubmissionStatus.needs_assistance, "user_resolves_action_needed"): SubmissionStatus.awaiting_research_review,
    # From it_rejected
    (SubmissionStatus.it_rejected, "user_requests_assistance"): SubmissionStatus.needs_assistance,
}


def transition(
    current: SubmissionStatus,
    event: TransitionEvent,
    emit_metric: Callable[[SubmissionStatus], None] | None = None,
) -> SubmissionStatus:
    """Pure state transition function.

    Arguments:
        current: Current submission status.
        event: Triggering event.
        emit_metric: Optional callback to emit metrics (e.g., for observability).
                     Called with the new state if transition succeeds.

    Returns:
        New submission status.

    Raises:
        InvalidTransitionError: If the transition is not allowed.
    """
    # Look up the transition
    new_status = _TRANSITION_TABLE.get((current, event))
    if new_status is None:
        raise InvalidTransitionError(current, event)

    # Call metric callback if provided
    if emit_metric:
        emit_metric(new_status)

    return new_status


def is_terminal_state(status: SubmissionStatus) -> bool:
    """Check if a status is a terminal (sink) state.

    Terminal states cannot transition to any other state.
    """
    # deployed, it_rejected, research_rejected, scan_rejected are sinks
    return status in {
        SubmissionStatus.deployed,
        SubmissionStatus.it_rejected,
        SubmissionStatus.research_rejected,
        SubmissionStatus.scan_rejected,
    }
