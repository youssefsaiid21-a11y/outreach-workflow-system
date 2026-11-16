"""
Enumerations for the orchestration module.

This file defines every enum used throughout the orchestration engine. These
enums appear in:
  - Sequence config definitions (StepType determines what each step does)
  - Webhook/event ingestion (EventType classifies inbound signals)
  - The FSM state machine (OrchestrationStatus tracks where a lead is)
  - Handler results (StepResultStatus, ProviderStatus)
  - Manual task management (TaskPriority, TaskStatus)

All enums use (str, Enum) so values serialise transparently to/from JSON
and SQLAlchemy String columns without custom type adapters. This means
you can compare ``status == "active"`` or ``status == OrchestrationStatus.active``
interchangeably, and JSON serialisation works without a custom encoder.
"""

from enum import Enum


# ---------------------------------------------------------------------------
# Step types — what each step in a sequence does
# ---------------------------------------------------------------------------


class StepType(str, Enum):
    """The kind of action that a sequence step performs.

    Each value maps to a handler class in handlers.py via the dispatcher's
    _handlers dict. Adding a new StepType requires a corresponding handler.

    As of 2026-06, only LinkedIn steps are active in production. Email steps
    exist in legacy sequences but are silently skipped by SendEmailRemovedHandler.
    """

    send_email = "send_email"                        # Legacy — skipped in prod (see SendEmailRemovedHandler)
    send_linkedin_connect = "send_linkedin_connect"  # Queues a LinkedIn connection request via PhantomBuster
    send_linkedin_message = "send_linkedin_message"  # Queues a LinkedIn DM via PhantomBuster
    create_manual_task = "create_manual_task"         # Creates a task for the sales rep to act on manually
    wait = "wait"                                     # No-op pause step; timing handled by scheduler's day_offset
    end_sequence = "end_sequence"                     # Triggers the "sequence_ended" FSM transition → completed
    # Stubs  -  not yet wired to a provider. Dispatched to CreateManualTaskHandler as a fallback.
    send_call_task = "send_call_task"
    send_whatsapp = "send_whatsapp"


# ---------------------------------------------------------------------------
# Event types — inbound signals from providers and manual actions
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    """All engagement / system events that the orchestration layer understands.

    These are normalised from raw webhook payloads by ingestion.py's
    EVENT_TYPE_MAP. The ingestion layer maps provider-specific event strings
    (e.g. PhantomBuster's "connection_request_sent") to these canonical types.

    Some events drive FSM stop transitions (see state_machine.STOP_EVENT_MAP),
    while others only update engagement scores or prospect status columns.
    """

    # Email channel — events from email sending providers
    email_sent = "email_sent"                        # Confirmation that an email was dispatched
    email_opened = "email_opened"                    # Pixel-tracked open (unreliable; deduped in ingestion)
    email_clicked = "email_clicked"                  # Link click (deduped within SESSION_DEDUP_WINDOW_SECONDS)
    email_replied_positive = "email_replied_positive"  # Reply classified as interested → STOPS sequence
    email_replied_negative = "email_replied_negative"  # Reply classified as not interested → STOPS sequence
    email_bounced_hard = "email_bounced_hard"        # Permanent delivery failure (suppresses email channel only)
    email_bounced_soft = "email_bounced_soft"         # Transient delivery failure (retried automatically)
    unsubscribed = "unsubscribed"                    # Opt-out request → STOPS sequence

    # LinkedIn channel — events from PhantomBuster results or manual UI actions
    linkedin_connect_sent = "linkedin_connect_sent"          # Connection request dispatched by PB
    linkedin_connect_accepted = "linkedin_connect_accepted"  # Target accepted the connection
    linkedin_message_sent = "linkedin_message_sent"          # DM sent by PB Message Sender phantom
    linkedin_message_replied = "linkedin_message_replied"    # Target replied to our DM

    # Qualification / routing — from CRM or manual review
    lead_disqualified = "lead_disqualified"          # Lead doesn't match ICP → STOPS sequence

    # Intent signals — from website tracking (future)
    website_visit_signal = "website_visit_signal"

    # System / data quality — provider health events
    duplicate_crm_conflict = "duplicate_crm_conflict"
    provider_quota_exceeded = "provider_quota_exceeded"
    provider_outage = "provider_outage"


# ---------------------------------------------------------------------------
# Orchestration status — the FSM states a lead can be in
# ---------------------------------------------------------------------------


class OrchestrationStatus(str, Enum):
    """Lifecycle status of a lead inside a sequence.

    The FSM in state_machine.py defines which transitions between these
    statuses are valid. The lifecycle is:

        pending → active ⇄ waiting_for_next_step → completed
                    ↓                    ↓
                  paused              paused
                    ↓                    ↓
              stopped_*              stopped_*

    Terminal statuses (stopped_* and completed) cannot transition further.
    """

    # Non-terminal statuses — sequence is still in progress
    pending = "pending"                          # Enrolled but first step not yet executed
    active = "active"                            # Currently executing or about to execute a step
    waiting_for_next_step = "waiting_for_next_step"  # Step done; waiting for scheduled_next_at
    paused = "paused"                            # Manually paused by rep or auto-paused on failure

    # Terminal statuses — sequence ends here, no further steps dispatched
    stopped_positive_reply = "stopped_positive_reply"
    stopped_negative_reply = "stopped_negative_reply"
    stopped_unsubscribed = "stopped_unsubscribed"
    # stopped_bounced is now only reached via the bounce circuit breaker
    # (mass-stop on >10% bounce rate) or explicit admin action. Single-lead
    # hard bounces are handled as channel suppression, not a terminal state,
    # so LinkedIn / manual-task steps continue to dispatch. See
    # state_machine.STOP_EVENT_MAP. (Email was removed from campaign outreach
    # in 2026-06; send_email steps are now inert  -  see
    # handlers.SendEmailRemovedHandler.)
    stopped_bounced = "stopped_bounced"
    stopped_disqualified = "stopped_disqualified"
    completed = "completed"                      # All steps executed normally


# ---------------------------------------------------------------------------
# Manual task enums — for human-in-the-loop actions
# ---------------------------------------------------------------------------


class TaskPriority(str, Enum):
    """Priority levels for manual tasks surfaced to the sales rep.

    Tasks are created automatically by:
    - High engagement score crossing threshold → medium/high priority
    - LinkedIn message fallback (not connected) → medium priority
    - Key account hard bounce → urgent priority
    """

    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class TaskStatus(str, Enum):
    """Lifecycle of a manual task. Reps update these via the dashboard UI."""

    open = "open"              # Newly created, awaiting rep action
    in_progress = "in_progress"  # Rep has acknowledged and is working on it
    completed = "completed"    # Rep has finished the task
    dismissed = "dismissed"    # Rep decided no action needed


# ---------------------------------------------------------------------------
# Step result and provider health enums
# ---------------------------------------------------------------------------


class StepResultStatus(str, Enum):
    """Result of attempting to execute a single sequence step.

    The dispatcher uses these to decide what happens next:
    - executed: step succeeded; advance to next step
    - skipped: step intentionally not run (condition not met, already done, etc.);
               advance to next step
    - failed: step errored; enters retry ladder, eventually pauses sequence
    - deferred: transient issue (quota, network); reschedule 1 hour forward
    """

    executed = "executed"
    skipped = "skipped"
    failed = "failed"
    deferred = "deferred"


class ProviderStatus(str, Enum):
    """Health status reported by an external provider (PhantomBuster, etc.).

    Used by the provider abstraction layer to communicate availability.
    """

    ok = "ok"
    quota_exceeded = "quota_exceeded"
    not_connected = "not_connected"
    outage = "outage"
