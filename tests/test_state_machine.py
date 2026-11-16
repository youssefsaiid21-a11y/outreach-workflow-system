"""
Tests for the orchestration state machine.

Verifies:
  - All valid transitions succeed
  - Invalid transitions raise ValueError
  - Terminal states reject further transitions
  - Event-to-trigger mapping works correctly
  - Audit logs are written on every transition
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.orchestration.enums import OrchestrationStatus, EventType
from src.orchestration.models import LeadSequenceState, EngagementEvent
from src.orchestration.state_machine import (
    TRANSITION_TABLE,
    STOP_EVENT_MAP,
    is_terminal,
    transition_state,
    apply_event_to_state,
)


def _make_state(status: OrchestrationStatus = OrchestrationStatus.pending) -> LeadSequenceState:
    return LeadSequenceState(
        state_id="test-state-1",
        lead_id="lead-1",
        sequence_id="seq-1",
        status=status,
        enrolled_at=datetime.now(timezone.utc),
    )


def _make_event(event_type: EventType) -> EngagementEvent:
    return EngagementEvent(
        event_id="evt-1",
        lead_id="lead-1",
        event_type=event_type,
        source="test",
        occurred_at=datetime.now(timezone.utc),
    )


def _mock_audit_repo() -> MagicMock:
    repo = MagicMock()
    repo.write = MagicMock()
    return repo


# ── Terminal state detection ─────────────────────────────────────────────────

class TestIsTerminal:
    def test_terminal_statuses(self):
        for status in [
            OrchestrationStatus.stopped_positive_reply,
            OrchestrationStatus.stopped_negative_reply,
            OrchestrationStatus.stopped_unsubscribed,
            OrchestrationStatus.stopped_bounced,
            OrchestrationStatus.stopped_disqualified,
            OrchestrationStatus.completed,
        ]:
            assert is_terminal(status), f"{status} should be terminal"

    def test_non_terminal_statuses(self):
        for status in [
            OrchestrationStatus.pending,
            OrchestrationStatus.active,
            OrchestrationStatus.waiting_for_next_step,
            OrchestrationStatus.paused,
        ]:
            assert not is_terminal(status), f"{status} should NOT be terminal"


# ── Valid transitions ────────────────────────────────────────────────────────

class TestValidTransitions:
    def test_pending_to_active(self):
        state = _make_state(OrchestrationStatus.pending)
        audit = _mock_audit_repo()
        result = transition_state(state, "enrolled", None, audit)
        assert result.status == OrchestrationStatus.active
        audit.write.assert_called_once()

    def test_active_to_waiting(self):
        state = _make_state(OrchestrationStatus.active)
        audit = _mock_audit_repo()
        result = transition_state(state, "step_executed", None, audit)
        assert result.status == OrchestrationStatus.waiting_for_next_step

    def test_waiting_to_active(self):
        state = _make_state(OrchestrationStatus.waiting_for_next_step)
        audit = _mock_audit_repo()
        result = transition_state(state, "step_due", None, audit)
        assert result.status == OrchestrationStatus.active

    def test_active_pause_resume_cycle(self):
        state = _make_state(OrchestrationStatus.active)
        audit = _mock_audit_repo()
        state = transition_state(state, "pause", None, audit)
        assert state.status == OrchestrationStatus.paused
        state = transition_state(state, "resume", None, audit)
        assert state.status == OrchestrationStatus.active

    def test_active_to_completed(self):
        state = _make_state(OrchestrationStatus.active)
        audit = _mock_audit_repo()
        result = transition_state(state, "sequence_ended", None, audit)
        assert result.status == OrchestrationStatus.completed

    def test_waiting_to_completed(self):
        state = _make_state(OrchestrationStatus.waiting_for_next_step)
        audit = _mock_audit_repo()
        result = transition_state(state, "sequence_ended", None, audit)
        assert result.status == OrchestrationStatus.completed


# ── Stop transitions from every non-terminal status ─────────────────────────

class TestStopTransitions:
    @pytest.mark.parametrize("from_status", [
        OrchestrationStatus.active,
        OrchestrationStatus.waiting_for_next_step,
        OrchestrationStatus.paused,
        OrchestrationStatus.pending,
    ])
    @pytest.mark.parametrize("trigger,expected", [
        ("event_replied_positive", OrchestrationStatus.stopped_positive_reply),
        ("event_replied_negative", OrchestrationStatus.stopped_negative_reply),
        ("event_unsubscribed", OrchestrationStatus.stopped_unsubscribed),
        ("event_bounced", OrchestrationStatus.stopped_bounced),
        ("event_disqualified", OrchestrationStatus.stopped_disqualified),
    ])
    def test_stop_from_any_non_terminal(self, from_status, trigger, expected):
        state = _make_state(from_status)
        audit = _mock_audit_repo()
        result = transition_state(state, trigger, None, audit)
        assert result.status == expected


# ── Invalid transitions ──────────────────────────────────────────────────────

class TestInvalidTransitions:
    def test_completed_rejects_all(self):
        state = _make_state(OrchestrationStatus.completed)
        audit = _mock_audit_repo()
        with pytest.raises(ValueError, match="Invalid transition"):
            transition_state(state, "enrolled", None, audit)

    def test_pending_rejects_step_executed(self):
        state = _make_state(OrchestrationStatus.pending)
        audit = _mock_audit_repo()
        with pytest.raises(ValueError, match="Invalid transition"):
            transition_state(state, "step_executed", None, audit)

    def test_paused_rejects_step_due(self):
        state = _make_state(OrchestrationStatus.paused)
        audit = _mock_audit_repo()
        with pytest.raises(ValueError, match="Invalid transition"):
            transition_state(state, "step_due", None, audit)


# ── Event application ────────────────────────────────────────────────────────

class TestApplyEvent:
    def test_positive_reply_stops_active(self):
        state = _make_state(OrchestrationStatus.active)
        event = _make_event(EventType.email_replied_positive)
        audit = _mock_audit_repo()
        result = apply_event_to_state(state, event, audit)
        assert result.status == OrchestrationStatus.stopped_positive_reply

    def test_hard_bounce_does_not_stop_sequence(self):
        """2026-04-10 channel-suppression fix: hard bounces no longer
        terminate the orchestration sequence. The email channel is marked
        bounced on EmailSequence.status by the ingestion handler and
        SendEmailHandler skips future email steps, but LinkedIn / manual
        task / other channels continue to dispatch - so apply_event_to_state
        must leave the orchestration state unchanged on a hard bounce."""
        state = _make_state(OrchestrationStatus.waiting_for_next_step)
        event = _make_event(EventType.email_bounced_hard)
        audit = _mock_audit_repo()
        result = apply_event_to_state(state, event, audit)
        assert result.status == OrchestrationStatus.waiting_for_next_step

    def test_stopped_bounced_still_reachable_via_transition_state(self):
        """The terminal stopped_bounced status stays valid for campaign-level
        mass stops (bounce circuit breaker) and manual admin actions."""
        state = _make_state(OrchestrationStatus.active)
        audit = _mock_audit_repo()
        result = transition_state(state, "event_bounced", None, audit)
        assert result.status == OrchestrationStatus.stopped_bounced

    def test_soft_bounce_does_not_stop(self):
        """Soft bounces are transient - should NOT stop the sequence."""
        state = _make_state(OrchestrationStatus.active)
        event = _make_event(EventType.email_bounced_soft)
        audit = _mock_audit_repo()
        result = apply_event_to_state(state, event, audit)
        assert result.status == OrchestrationStatus.active  # unchanged

    def test_email_opened_does_not_change_state(self):
        """Opens are informational - no state change."""
        state = _make_state(OrchestrationStatus.active)
        event = _make_event(EventType.email_opened)
        audit = _mock_audit_repo()
        result = apply_event_to_state(state, event, audit)
        assert result.status == OrchestrationStatus.active

    def test_terminal_state_ignores_events(self):
        state = _make_state(OrchestrationStatus.stopped_positive_reply)
        event = _make_event(EventType.email_replied_negative)
        audit = _mock_audit_repo()
        result = apply_event_to_state(state, event, audit)
        assert result.status == OrchestrationStatus.stopped_positive_reply  # unchanged


# ── Transition table completeness ────────────────────────────────────────────

class TestTransitionTableCompleteness:
    def test_all_stop_events_have_transitions_from_active(self):
        """Every event in STOP_EVENT_MAP should have a transition from active status."""
        for event_type, trigger in STOP_EVENT_MAP.items():
            key = (OrchestrationStatus.active, trigger)
            assert key in TRANSITION_TABLE, (
                f"STOP_EVENT_MAP maps {event_type} to trigger '{trigger}' "
                f"but TRANSITION_TABLE has no entry for (active, '{trigger}')"
            )

    def test_no_transitions_from_terminal_states(self):
        """Terminal states should have zero outbound transitions."""
        terminal = {s for s in OrchestrationStatus if is_terminal(s)}
        for (from_status, _trigger), _to in TRANSITION_TABLE.items():
            assert from_status not in terminal, (
                f"TRANSITION_TABLE has a transition from terminal state {from_status}"
            )

    def test_audit_written_on_every_transition(self):
        """Every successful transition must write exactly one audit log entry."""
        for (from_status, trigger), _to_status in TRANSITION_TABLE.items():
            state = _make_state(from_status)
            audit = _mock_audit_repo()
            transition_state(state, trigger, None, audit)
            assert audit.write.call_count == 1, (
                f"Transition ({from_status}, {trigger}) did not write exactly 1 audit log"
            )
