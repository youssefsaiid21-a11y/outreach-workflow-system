"""
Tests for orchestration step condition evaluation.

All external dependencies (event_repo) are mocked.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from src.orchestration.conditions import (
    _check_no_reply,
    _check_no_unsubscribe,
    _check_linkedin_url_exists,
    _check_linkedin_connected,
    evaluate_conditions,
    step_should_execute,
)
from src.orchestration.enums import EventType, OrchestrationStatus, StepType
from src.orchestration.models import (
    LeadContext,
    LeadSequenceState,
    SequenceStep,
    StepCondition,
)


def _make_lead(**kwargs) -> LeadContext:
    defaults = {
        "lead_id": "lead-1",
        "email": "john@example.com",
        "first_name": "John",
        "last_name": "Doe",
        "company": "Acme",
        "title": "VP Sales",
        "industry": "SaaS",
        "location": "US",
    }
    defaults.update(kwargs)
    return LeadContext(**defaults)


def _make_state() -> LeadSequenceState:
    return LeadSequenceState(
        state_id="state-1",
        lead_id="lead-1",
        sequence_id="seq-1",
        status=OrchestrationStatus.active,
        enrolled_at=datetime.now(timezone.utc),
    )


def _make_step(conditions=None, enabled=True) -> SequenceStep:
    return SequenceStep(
        step_id="step-1",
        step_type=StepType.send_email,
        day_offset=0,
        conditions=conditions or [],
        enabled=enabled,
    )


def _mock_event_repo(has_positive=False, has_negative=False, has_unsub=False, has_linkedin_accept=False):
    repo = MagicMock()

    def get_by_lead(lead_id, event_type):
        if event_type == EventType.email_replied_positive and has_positive:
            return [MagicMock()]
        if event_type == EventType.email_replied_negative and has_negative:
            return [MagicMock()]
        if event_type == EventType.unsubscribed and has_unsub:
            return [MagicMock()]
        if event_type == EventType.linkedin_connect_accepted and has_linkedin_accept:
            return [MagicMock()]
        return []

    repo.get_by_lead = get_by_lead
    return repo


# ── Individual condition tests ───────────────────────────────────────────────

class TestNoReplyCondition:
    def test_no_reply_passes(self):
        repo = _mock_event_repo()
        passed, reason = _check_no_reply("lead-1", repo)
        assert passed is True

    def test_positive_reply_fails(self):
        repo = _mock_event_repo(has_positive=True)
        passed, reason = _check_no_reply("lead-1", repo)
        assert passed is False
        assert "reply_already_received" in reason

    def test_negative_reply_fails(self):
        repo = _mock_event_repo(has_negative=True)
        passed, reason = _check_no_reply("lead-1", repo)
        assert passed is False


class TestNoUnsubscribeCondition:
    def test_not_unsubscribed(self):
        repo = _mock_event_repo()
        passed, reason = _check_no_unsubscribe("lead-1", repo)
        assert passed is True

    def test_unsubscribed(self):
        repo = _mock_event_repo(has_unsub=True)
        passed, reason = _check_no_unsubscribe("lead-1", repo)
        assert passed is False
        assert "unsubscribed" in reason


class TestLinkedinUrlExists:
    def test_has_url(self):
        lead = _make_lead(linkedin_url="https://linkedin.com/in/johndoe")
        passed, reason = _check_linkedin_url_exists(lead)
        assert passed is True

    def test_no_url(self):
        lead = _make_lead(linkedin_url=None)
        passed, reason = _check_linkedin_url_exists(lead)
        assert passed is False

    def test_empty_url(self):
        lead = _make_lead(linkedin_url="  ")
        passed, reason = _check_linkedin_url_exists(lead)
        assert passed is False


class TestLinkedinConnected:
    def test_connected_via_flag(self):
        lead = _make_lead(linkedin_connected=True)
        repo = _mock_event_repo()
        passed, reason = _check_linkedin_connected(lead, repo)
        assert passed is True
        assert "flag" in reason

    def test_connected_via_event(self):
        lead = _make_lead(linkedin_connected=False)
        repo = _mock_event_repo(has_linkedin_accept=True)
        passed, reason = _check_linkedin_connected(lead, repo)
        assert passed is True
        assert "event" in reason

    def test_not_connected(self):
        lead = _make_lead(linkedin_connected=False)
        repo = _mock_event_repo()
        passed, reason = _check_linkedin_connected(lead, repo)
        assert passed is False


# ── evaluate_conditions tests ────────────────────────────────────────────────

class TestEvaluateConditions:
    def test_no_conditions(self):
        step = _make_step(conditions=[])
        lead = _make_lead()
        state = _make_state()
        repo = _mock_event_repo()
        passed, reason = evaluate_conditions(step, lead, state, repo)
        assert passed is True
        assert reason == "no_conditions"

    def test_single_passing_condition(self):
        step = _make_step(conditions=[
            StepCondition(condition_type="no_reply"),
        ])
        lead = _make_lead()
        state = _make_state()
        repo = _mock_event_repo()
        passed, reason = evaluate_conditions(step, lead, state, repo)
        assert passed is True

    def test_single_failing_condition(self):
        step = _make_step(conditions=[
            StepCondition(condition_type="no_reply"),
        ])
        lead = _make_lead()
        state = _make_state()
        repo = _mock_event_repo(has_positive=True)
        passed, reason = evaluate_conditions(step, lead, state, repo)
        assert passed is False
        assert "no_reply" in reason

    def test_multiple_conditions_all_pass(self):
        step = _make_step(conditions=[
            StepCondition(condition_type="no_reply"),
            StepCondition(condition_type="no_unsubscribe"),
        ])
        lead = _make_lead()
        state = _make_state()
        repo = _mock_event_repo()
        passed, reason = evaluate_conditions(step, lead, state, repo)
        assert passed is True

    def test_multiple_conditions_one_fails(self):
        step = _make_step(conditions=[
            StepCondition(condition_type="no_reply"),
            StepCondition(condition_type="no_unsubscribe"),
        ])
        lead = _make_lead()
        state = _make_state()
        repo = _mock_event_repo(has_unsub=True)
        passed, reason = evaluate_conditions(step, lead, state, repo)
        assert passed is False
        assert "unsubscribe" in reason

    def test_unknown_condition_fails_closed(self):
        """Unknown condition types should fail-closed, not fail-open."""
        step = _make_step(conditions=[
            StepCondition(condition_type="nonexistent_condition"),
        ])
        lead = _make_lead()
        state = _make_state()
        repo = _mock_event_repo()
        passed, reason = evaluate_conditions(step, lead, state, repo)
        assert passed is False
        assert "unknown_condition_type" in reason


# ── step_should_execute tests ────────────────────────────────────────────────

class TestStepShouldExecute:
    def test_disabled_step(self):
        step = _make_step(enabled=False)
        lead = _make_lead()
        state = _make_state()
        repo = _mock_event_repo()
        passed, reason = step_should_execute(step, lead, state, repo)
        assert passed is False
        assert reason == "step_disabled"

    def test_enabled_step_no_conditions(self):
        step = _make_step(enabled=True, conditions=[])
        lead = _make_lead()
        state = _make_state()
        repo = _mock_event_repo()
        passed, reason = step_should_execute(step, lead, state, repo)
        assert passed is True

    def test_enabled_step_with_failing_condition(self):
        step = _make_step(enabled=True, conditions=[
            StepCondition(condition_type="linkedin_url_exists"),
        ])
        lead = _make_lead(linkedin_url=None)
        state = _make_state()
        repo = _mock_event_repo()
        passed, reason = step_should_execute(step, lead, state, repo)
        assert passed is False
        assert "linkedin_url" in reason
