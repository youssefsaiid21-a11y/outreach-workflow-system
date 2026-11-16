"""
SQLAlchemy ORM models — the complete data schema for the Example outreach system.

This file defines every database table. The key entity relationships are:

  User (BD rep)
    └── Campaign (outreach campaign targeting an audience segment)
         └── EmailSequence (per-prospect generated LinkedIn messages + email content)
              └── Prospect (individual contact to reach out to)
    └── StyleProfile (writing style analysis used to personalise AI-generated copy)
    └── UserInbox (linked Outlook sending inboxes — legacy, email sending removed)
    └── UserCredential (encrypted PhantomBuster API keys per user)

  AudienceSegment ← Prospect (prospects are grouped into ICP segments)
  Account ← Prospect (company-level grouping by email domain)

  LinkedInQueue (batch queue for PhantomBuster actions — connect/message)
  OrchSequenceStateORM (see src/orchestration/orm.py — tracks sequence progress)

  ConferenceLead (one-off conference outreach, separate from campaigns)
  Newsletter → NewsletterRecipient (bulk email sends via Graph API)

  FollowupDeal (cached HubSpot deals for the follow-ups machine)
  GtmManualEntry (manual GTM metrics — meetings, referrals, inbound leads)

Database:
  - PostgreSQL in production (VPS), SQLite for local dev/tests
  - Migrations: Alembic (alembic/versions/) for prod, plus a hardcoded migration
    list in database.py for columns added between Alembic runs
  - All datetime columns use UTC (see _utcnow helper)

Related files:
  - src/db/database.py           — engine, session factory, migration runner
  - src/orchestration/orm.py     — OrchSequenceStateORM, OrchAuditLogORM
  - alembic/versions/            — Alembic migration scripts
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Text, Date, DateTime,
    ForeignKey, JSON, Boolean, Enum, Index
)
from sqlalchemy.orm import relationship
import enum
from .database import Base


def _utcnow():
    """Default factory for DateTime columns — always UTC, never naive."""
    return datetime.now(timezone.utc)


# ── User & Authentication ────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    """User roles for access control. Admin can manage all users and reset passwords."""
    admin = "admin"
    rep = "rep"     # BD (Business Development) representative


class User(Base):
    """BD rep account. Each user owns their own campaigns, prospects, and credentials.
    Multi-tenancy is enforced by user_id foreign keys on most tables, so one rep
    cannot see or modify another rep's data."""
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)
    # FIX-2: bcrypt hash of the per-user API key secret. Format on the wire is
    # `<user_id>.<secret>`. nullable=True for legacy users that have not minted
    # a key yet (they continue to use JWT or the legacy env-var key).
    api_key_hash = Column(String, nullable=True)
    role = Column(Enum(UserRole), default=UserRole.rep)
    created_at = Column(DateTime, default=_utcnow)

    campaigns = relationship("Campaign", back_populates="user")
    prospects = relationship("Prospect", back_populates="user")
    jobs = relationship("Job", back_populates="user")
    style_profiles = relationship("StyleProfile", back_populates="user")
    user_inboxes = relationship("UserInbox", back_populates="user")


class UserInbox(Base):
    """Per-user inbox linking (legacy — direct email sending via Instantly was removed in
    March 2026). Kept for schema compatibility; may be repurposed if email sending returns."""
    __tablename__ = "user_inboxes"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    inbox_email = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)

    user = relationship("User", back_populates="user_inboxes")

    __table_args__ = (
        Index("ix_user_inboxes_user_email", "user_id", "inbox_email", unique=True),
    )


# ── Account & Prospect ────────────────────────────────────────────────────────

class Account(Base):
    """Company-level grouping of prospects, auto-created from email domains.
    Allows account-based analytics (e.g. "how many contacts at Shell have we reached?")."""
    __tablename__ = "accounts"

    id = Column(String, primary_key=True)
    domain = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=True)  # company name (from first prospect)
    industry = Column(String, nullable=True)
    employee_count = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    prospects = relationship("Prospect", back_populates="account")


class ProspectStatus(str, enum.Enum):
    """Lifecycle status of a prospect through the LinkedIn outreach funnel.
    Transitions: new → pending → enrolled → connected → messaged → replied.
    Terminal statuses (enrolled+) prevent re-enrollment in new campaigns."""
    new = "new"                 # freshly imported, not yet assigned to any campaign
    pending = "pending"         # assigned to a campaign, sequences generated, not yet launched
    enrolled = "enrolled"       # LinkedIn connect request queued/sent via PhantomBuster
    connected = "connected"     # connection accepted (confirmed by PB result polling)
    messaged = "messaged"       # follow-up message sent after connection
    replied = "replied"         # prospect replied to message
    opened = "opened"           # legacy (email open tracking), kept for backward compat
    bounced = "bounced"         # legacy (email bounce), kept for backward compat
    unsubscribed = "unsubscribed"  # manually suppressed or auto-suppressed on cancel


class CampaignStatus(str, enum.Enum):
    """Campaign lifecycle. Active campaigns have their sequences advancing via
    the orchestration scheduler. Cancelled is terminal (non-resumable)."""
    draft = "draft"             # created, sequences may be generated but not launched
    launching = "launching"     # transitional: launch in progress (prevents double-launch)
    active = "active"           # live: orchestration scheduler is advancing sequences
    paused = "paused"           # manually paused by operator; can be resumed
    completed = "completed"     # all sequences finished their steps
    archived = "archived"       # hidden from default list view, but data preserved
    cancelled = "cancelled"     # hard-cancel; non-resumable; orch states stopped


class SequenceStatus(str, enum.Enum):
    """Per-prospect sequence status within a campaign. Mirrors ProspectStatus
    but scoped to a single campaign (a prospect can be in different campaigns
    at different stages)."""
    pending = "pending"
    enrolled = "enrolled"        # connect request sent
    connected = "connected"      # connection accepted
    messaged = "messaged"        # follow-up message sent
    replied = "replied"          # prospect replied to message
    opened = "opened"            # legacy (email), kept for backward compat
    bounced = "bounced"          # legacy (email), kept for backward compat
    unsubscribed = "unsubscribed"


class Prospect(Base):
    """Individual contact to reach out to. Imported from Apollo.io search results
    or manually via CSV. Each prospect belongs to one user (multi-tenant isolation)
    and can be assigned to multiple campaigns via EmailSequence join rows.

    Key fields:
      - linkedin_url: required for LinkedIn outreach (prospects without it are skipped at launch)
      - email_validation_status: set by Reacher verification; invalid emails blocked at launch
      - engagement_score: incremented by opens/clicks/replies (used for prioritisation)
      - apollo_id: links back to the Apollo.io contact record (for dedup on re-import)
    """
    __tablename__ = "prospects"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    # nullable=True + unique=True allows multiple NULLs in SQL (NULL != NULL).
    # The partial index below enforces uniqueness only for non-null values.
    apollo_id = Column(String, nullable=True, index=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=True)
    email = Column(String, nullable=False, index=True)
    title = Column(String, nullable=True)
    company = Column(String, nullable=True)
    industry = Column(String, nullable=True)
    headcount = Column(Integer, nullable=True)
    location = Column(String, nullable=True)
    linkedin_url = Column(String, nullable=True)
    company_website = Column(String, nullable=True)
    account_id = Column(String, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    segment_id = Column(String, ForeignKey("audience_segments.id", ondelete="SET NULL"), nullable=True, index=True)
    linkedin_headline = Column(String, nullable=True)
    linkedin_summary = Column(Text, nullable=True)
    company_description = Column(Text, nullable=True)
    enriched_at = Column(DateTime, nullable=True)
    status = Column(Enum(ProspectStatus), default=ProspectStatus.new, index=True)
    email_validation_status = Column(String, nullable=True, index=True)
    email_validation_sub_status = Column(String, nullable=True)
    email_validated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    engagement_score = Column(Integer, default=0)
    # LinkedIn orchestration fields (added for sequence tracking)
    linkedin_connected = Column(Boolean, default=False, nullable=True)
    linkedin_connect_sent_at = Column(DateTime, nullable=True)

    sequences = relationship("EmailSequence", back_populates="prospect")
    segment = relationship("AudienceSegment", back_populates="prospects")
    user = relationship("User", back_populates="prospects")
    account = relationship("Account", back_populates="prospects")

    __table_args__ = (
        # Partial unique index: enforce uniqueness for non-null apollo_id values.
        Index(
            "ix_prospects_apollo_id_unique",
            "apollo_id",
            unique=True,
            sqlite_where=Column("apollo_id").isnot(None),
        ),
        # FIX-9: Per-user uniqueness on (user_id, email) so two reps can each
        # import the same lead without one silently dropping the other's row.
        # The legacy global ``ix_prospects_email_unique`` index must be dropped
        # in production manually (MANUAL-2)  -  until then it continues to work
        # for the single-rep prod data.
        Index(
            "ix_prospects_user_email",
            "user_id", "email",
            unique=True,
        ),
    )


class ApolloRevealCache(Base):
    """Cache of every Apollo contact we've paid to reveal.

    Audit 2026-05-05: prevents paying twice for the same contact.
    If a prospect save fails (rollback, integrity error, etc.), the
    revealed data survives here and can be re-imported for free.
    """
    __tablename__ = "apollo_reveal_cache"

    apollo_id = Column(String, primary_key=True)
    email = Column(String, nullable=True, index=True)
    raw_json = Column(JSON, nullable=False)
    revealed_at = Column(DateTime, default=_utcnow)


# ── Campaign & Sequence ──────────────────────────────────────────────────────

class AudienceSegment(Base):
    """ICP (Ideal Customer Profile) segment — a saved set of Apollo search filters
    (job titles, industries, company sizes, geographies). Prospects are imported
    into a segment, then campaigns target a segment or explicit prospect list."""
    __tablename__ = "audience_segments"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    filters = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    campaigns = relationship("Campaign", back_populates="segment")
    prospects = relationship("Prospect", back_populates="segment")


class Campaign(Base):
    """An outreach campaign targeting a set of prospects with AI-generated LinkedIn
    messages. The workflow is:
      1. Create campaign (draft) with goal, value prop, and audience (segment or explicit IDs)
      2. Generate sequences — Claude creates personalised connect notes + follow-up messages
      3. Preview and approve — operator reviews and edits the generated content
      4. Launch — prospects are enrolled into the orchestration state machine
      5. The orchestration scheduler advances sequences over time (connect → wait → message)

    Each campaign is pinned to a SequenceConfig that defines the step types, day
    offsets, and conditions. Pre-0009 campaigns inherit the global config at launch.
    """
    __tablename__ = "campaigns"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String, nullable=False)
    goal = Column(Text, nullable=False)          # e.g. "Book HSE meetings in GCC oil & gas"
    value_prop = Column(Text, nullable=False)     # e.g. "AI-powered safety monitoring reduces incidents by 40%"
    segment_id = Column(String, ForeignKey("audience_segments.id", ondelete="SET NULL"), nullable=True)
    status = Column(Enum(CampaignStatus), default=CampaignStatus.draft, index=True)
    inbox_ids = Column(JSON, default=list)
    sending_limit_per_day = Column(Integer, default=30)
    step1_delay_days = Column(Integer, default=0)
    step2_delay_days = Column(Integer, default=4)
    step3_delay_days = Column(Integer, default=6)
    prospect_ids = Column(JSON, default=list)  # explicit prospect selection (overrides segment)
    ab_test_enabled = Column(Boolean, default=False)
    tokens_input = Column(Integer, default=0)    # Anthropic input tokens used for email generation
    tokens_output = Column(Integer, default=0)   # Anthropic output tokens used for email generation
    created_at = Column(DateTime, default=_utcnow)
    launched_at = Column(DateTime, nullable=True)

    # 2026-04-10 / 0009 migration: per-campaign sequence selection.
    # NULL means "use the legacy global custom_sequence_config fallback
    # at launch time"  -  the pre-0009 behaviour that every in-flight
    # campaign (including Nasser v1.1) silently inherited.
    sequence_config_id = Column(
        String,
        ForeignKey("sequence_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Multi-account LinkedIn: which LinkedIn account scrapes leads vs sends outreach.
    # NULL = use the default/legacy global PhantomBuster configuration.
    scrape_account_id = Column(
        String,
        ForeignKey("linkedin_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    outreach_account_id = Column(
        String,
        ForeignKey("linkedin_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    segment = relationship("AudienceSegment", back_populates="campaigns")
    sequences = relationship("EmailSequence", back_populates="campaign")
    user = relationship("User", back_populates="campaigns")
    sequence_config = relationship("SequenceConfigORM", back_populates="campaigns")
    scrape_account = relationship("LinkedInAccount", foreign_keys=[scrape_account_id])
    outreach_account = relationship("LinkedInAccount", foreign_keys=[outreach_account_id])


class EmailSequence(Base):
    """Per-prospect generated content for a campaign. Despite the name "EmailSequence",
    this now primarily stores LinkedIn messages (the email sending path via Instantly
    was removed in March 2026). The email{1-6}_subject/body columns are legacy but
    still populated by the AI generation flow for hybrid sequences.

    Key fields:
      - linkedin_connect_note: the connection request message (~30 words max)
      - linkedin_message: the follow-up message sent after connection (~50 words max)
      - approved: must be True before the campaign can be launched
      - status: tracks this prospect's progress through the sequence
      - steps_json: future replacement for the 12 flat email columns (not yet active)
    """
    __tablename__ = "email_sequences"

    id = Column(String, primary_key=True)
    campaign_id = Column(String, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    prospect_id = Column(String, ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False, index=True)
    # Legacy email columns (email1 through email6, each with subject + body).
    # Kept for backward compatibility with existing campaign data.
    email1_subject = Column(Text, nullable=True)
    email1_body = Column(Text, nullable=True)
    email2_subject = Column(Text, nullable=True)
    email2_body = Column(Text, nullable=True)
    email3_subject = Column(Text, nullable=True)
    email3_body = Column(Text, nullable=True)
    email4_subject = Column(Text, nullable=True)
    email4_body = Column(Text, nullable=True)
    email5_subject = Column(Text, nullable=True)
    email5_body = Column(Text, nullable=True)
    email6_subject = Column(Text, nullable=True)
    email6_body = Column(Text, nullable=True)
    # 2026-04-11 hardcoded-values audit (migration 0010, additive only):
    # future home for an ordered list of sequence steps. Replaces the 12
    # flat email{n}_subject/body columns once the read path flips in a
    # later Tier 3b migration. Currently NULL for every row  -  no code
    # reads or writes it yet.
    steps_json = Column(JSON, nullable=True, default=None)
    linkedin_connect_note = Column(Text, nullable=True)
    linkedin_message = Column(Text, nullable=True)
    email1_subject_b = Column(Text, nullable=True)   # A/B test: variant B subject line for email 1
    variant_sent = Column(String, nullable=True)      # "A" or "B"  -  which variant was actually sent
    current_step = Column(Integer, default=1)
    status = Column(Enum(SequenceStatus), default=SequenceStatus.pending, index=True)
    approved = Column(Boolean, default=False)
    opened_at = Column(DateTime, nullable=True)
    replied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    campaign = relationship("Campaign", back_populates="sequences")
    prospect = relationship("Prospect", back_populates="sequences")

    # One sequence per prospect per campaign  -  prevents duplicate generation races.
    __table_args__ = (
        Index(
            "ix_email_sequences_campaign_prospect_unique",
            "campaign_id", "prospect_id",
            unique=True,
        ),
    )


class SequenceTemplate(Base):
    """Saved, reusable sequence (per-user, optionally shared with the workspace).

    2026-04-09: operators wanted a way to save a sequence they liked from one
    campaign and reuse it on another without re-running Claude. Templates store
    the rendered email bodies + LinkedIn steps as JSON, snapshotted from a
    source ``EmailSequence`` row.

    Scope: per-user. The owner can flip ``is_shared`` to expose the template
    to the rest of the workspace (other users see it under "Shared templates"
    in the picker). Only the owner can rename or delete.
    """
    __tablename__ = "sequence_templates"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    owner_user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    is_shared = Column(Boolean, default=False, nullable=False, index=True)
    # Snapshot of the source sequence as a list of step dicts:
    #   [{"type": "email"|"linkedin_connect"|"linkedin_message",
    #     "step_number": 1..6 (email) | null (linkedin),
    #     "subject": "..." (email only),
    #     "body": "..."},
    #    ...]
    # JSON instead of 6+2 flat columns so we can grow the shape without a
    # migration each time.
    steps_json = Column(JSON, default=list, nullable=False)
    step_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class SequenceConfigORM(Base):
    """Named, reusable orchestration sequence config (per-user, optionally shared).

    Distinct from ``SequenceTemplate``  -  templates snapshot rendered email
    bodies of a past campaign so an operator can clone the *content*.
    ``SequenceConfigORM`` stores the *structure*  -  step types, day_offsets,
    conditions, timezone, scoring thresholds  -  the thing the orchestration
    scheduler reads to decide what to do when.

    2026-04-10: before this table existed, there was a single global
    ``app_settings`` row keyed ``custom_sequence_config`` that held ONE
    config; every save overwrote it and campaigns silently inherited
    whatever was current at launch time. This table lets operators save
    multiple named configs, optionally share them with the workspace,
    and pin each campaign to the exact config it launched with via
    ``Campaign.sequence_config_id``.
    """
    __tablename__ = "sequence_configs"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    owner_user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_shared = Column(Boolean, default=False, nullable=False, index=True)
    # Full SequenceConfig pydantic model serialised as JSON.
    config_json = Column(JSON, nullable=False)
    step_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    campaigns = relationship("Campaign", back_populates="sequence_config")

    __table_args__ = (
        Index(
            "ix_sequence_configs_owner_name_unique",
            "owner_user_id", "name",
            unique=True,
        ),
    )


# ── Style & Content ──────────────────────────────────────────────────────────

class StyleProfile(Base):
    """Writing style analysis derived from sample emails (e.g. the user's sent folder).
    Used by the AI generation prompts to match the user's natural writing style —
    formality level, sentence length, greeting patterns, sign-off preferences, etc.
    Synced via the /api/style/sync endpoint which analyses recent Outlook emails."""
    __tablename__ = "style_profiles"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    synced_at = Column(DateTime, default=_utcnow)
    avg_word_count = Column(Float, nullable=True)
    formality = Column(String, nullable=True)
    greeting_patterns = Column(JSON, default=list)
    sign_off_patterns = Column(JSON, default=list)
    avg_sentence_length = Column(Float, nullable=True)
    uses_bullet_points = Column(Boolean, default=False)
    bullet_point_frequency = Column(Float, default=0.0)
    sample_emails = Column(JSON, default=list)
    raw_profile = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utcnow)

    user = relationship("User", back_populates="style_profiles")


# ── Conference Outreach ──────────────────────────────────────────────────────
# Conference outreach is a separate flow from campaigns. It handles one-off
# personalised emails to people met at conferences/events. Leads are imported
# from Excel/CSV, sequences are generated per-lead, and drafts are created
# in Outlook via Microsoft Graph API (not sent directly — rep reviews first).

class ConferenceInbox(Base):
    """A sending inbox for conference outreach drafts — Outlook (Graph) or IMAP."""
    __tablename__ = "conference_inboxes"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)           # display name
    email = Column(String, nullable=False)
    type = Column(String, nullable=False)           # "outlook" | "imap"
    imap_host = Column(String, nullable=True)
    imap_port = Column(Integer, nullable=True)
    imap_password = Column(String, nullable=True)   # app password / IMAP password
    imap_use_ssl = Column(Boolean, default=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow)


class ConferenceLead(Base):
    """A person met at a conference/event. Imported from Excel/CSV upload.
    Has its own email generation flow (separate from Campaign sequences) and
    reply tracking via the conference_reply_poller background thread.

    The generated_sequence JSON blob stores the full multi-step email sequence
    (email1 through emailN + LinkedIn steps) as a JSON array. Each step can be
    drafted individually via the sequence modal in the dashboard."""
    __tablename__ = "conference_leads"

    id = Column(String, primary_key=True)
    conference_name = Column(String, nullable=False)
    outreach_type = Column(String, nullable=False, default="post", server_default="post")  # "pre" or "post"
    event_date = Column(Date, nullable=True)  # for pre-conference: the event start date
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    email = Column(String, nullable=False)
    company = Column(String, nullable=True)
    title = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    linkedin_url = Column(String, nullable=True)
    company_website = Column(String, nullable=True)
    linkedin_headline = Column(String, nullable=True)
    company_description = Column(Text, nullable=True)
    cc = Column(String, nullable=True)                    # CC email address(es), comma-separated
    category = Column(String, nullable=True)              # "lead", "partner", "referral"
    raw_data = Column(Text, nullable=True)              # JSON blob of ALL Excel/CSV columns for this row
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    enriched_at = Column(DateTime, nullable=True)
    generated_subject = Column(Text, nullable=True)
    generated_body = Column(Text, nullable=True)
    generated_at = Column(DateTime, nullable=True)
    generated_sequence = Column(Text, nullable=True)    # JSON blob of full email sequence (email1..emailN + linkedin)
    sequence_generated_at = Column(DateTime, nullable=True)
    tokens_input = Column(Integer, default=0)     # Anthropic input tokens used
    tokens_output = Column(Integer, default=0)    # Anthropic output tokens used
    draft_message_id = Column(String, nullable=True)
    draft_created_at = Column(DateTime, nullable=True)
    # Events-reply-cutoff 2026-04-09: reply tracking for Conference leads.
    # When the background poller (src/services/conference_reply_poller.py)
    # sees an inbound message from this lead's email address after we
    # drafted our outreach, it sets ``replied_at`` and captures the reply
    # metadata below. Once ``replied_at`` is non-null, every draft route
    # (create_draft / draft_all / draft_sequence_step) refuses further
    # sends for this lead  -  the UI also shows a red "Replied" badge and
    # disables the sequence-modal step buttons. Reset via
    # POST /conference/leads/{id}/clear-reply for false-positive undo.
    replied_at = Column(DateTime, nullable=True)
    reply_subject = Column(Text, nullable=True)
    reply_snippet = Column(Text, nullable=True)     # bodyPreview from Graph, ~255 chars
    reply_from_email = Column(String, nullable=True)  # may differ from lead.email if they replied from a personal address
    # Events-reminders 2026-04-09: list of Graph event ids created as
    # follow-up reminders on the rep's own Outlook calendar when email 1
    # is drafted. Shape: {"email2": "<graph-id>", "email3": "<graph-id>", ...}
    # Populated by routes_drafts.create_draft, consumed by the reply
    # poller (which deletes all remaining events if the lead replies) and
    # by any per-step draft that fires for the step BEFORE its reminder
    # (so stale nudges get cancelled). Stored as JSON so we can cancel
    # individually without pulling the full generated_sequence blob.
    calendar_event_ids = Column(JSON, nullable=True, default=None)
    meeting_booked = Column(Boolean, default=False, nullable=False, server_default="false")
    user_note = Column(Text, nullable=True)            # Free-text note added by rep after outreach (separate from Excel import notes)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_conf_leads_conf_email_type", "conference_name", "email", "outreach_type", unique=True),
        Index("ix_conf_leads_user_replied", "user_id", "replied_at"),
    )


class Job(Base):
    """Background job for long-running operations (email generation, enrichment, etc.)."""
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    type = Column(String, nullable=False)           # e.g. "generate_sequences"
    status = Column(String, default="pending")      # pending | running | done | failed
    payload = Column(JSON, default=dict)            # input parameters
    result = Column(JSON, nullable=True)            # output when done
    error = Column(Text, nullable=True)             # error message if failed
    progress = Column(Integer, default=0)           # 0-100
    progress_detail = Column(String, nullable=True) # human-readable status message
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Index for the idempotency check in start_generate_job  -  avoids full table scans.
    __table_args__ = (
        Index("ix_jobs_type_status", "type", "status"),
    )

    user = relationship("User", back_populates="jobs")


# ── Logging & Tracking ───────────────────────────────────────────────────────

class CreditLog(Base):
    """Tracks API credit consumption for cost visibility. Apollo reveal credits,
    Reacher email verification credits, etc. Displayed on the dashboard analytics page."""
    __tablename__ = "credit_logs"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    provider = Column(String, nullable=False)   # "apollo" | "reacher" | "proxycurl" | "hunter"
    operation = Column(String, nullable=False)  # "reveal" | "validate" | "enrich"
    credits_used = Column(Integer, default=1)
    context = Column(String, nullable=True)     # e.g. "segment: gcc-oilandgas-hse"
    created_at = Column(DateTime, default=_utcnow)


class SendLog(Base):
    """Audit trail for every outreach action sent. Records which email/message was
    sent to which prospect, from which inbox, at what time. Used for campaign
    analytics (sent/bounced/replied counts) and duplicate-send prevention."""
    __tablename__ = "send_logs"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    # SWAT-H12: added index=True for FK columns used in joins/filters
    campaign_id = Column(String, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    prospect_id = Column(String, ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence_id = Column(String, ForeignKey("email_sequences.id", ondelete="CASCADE"), nullable=False, index=True)
    step = Column(Integer, nullable=False)
    subject = Column(Text, nullable=True)
    body_preview = Column(Text, nullable=True)
    status = Column(String, default="pending", index=True)  # "pending" | "sent" | "bounced" | "failed"
    enrolled_at = Column(DateTime, default=_utcnow)         # when enrolled in orchestration
    sent_at = Column(DateTime, nullable=True)               # set when confirmed by webhook
    inbox_email = Column(String, nullable=True)


class UserCredential(Base):
    """Per-user encrypted credentials for external services (PhantomBuster, etc.)."""
    __tablename__ = "user_credentials"

    id = Column(String, primary_key=True, default=lambda: str(__import__("uuid").uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    service = Column(String, nullable=False)       # "phantombuster", etc.
    key_name = Column(String, nullable=False)       # "api_key", "connect_agent_id", etc.
    encrypted_value = Column(Text, nullable=False)  # Fernet-encrypted
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_user_cred_unique", "user_id", "service", "key_name", unique=True),
    )


class LinkedInAccount(Base):
    """A LinkedIn account configured for automation via PhantomBuster.

    Each account represents one person's LinkedIn profile + their PB phantom setup.
    Not every account has all capabilities — Vishnu might have Sales Nav but no
    outreach phantoms. Jun Hao might have outreach but no Sales Nav.

    Campaigns reference accounts via scrape_account_id (who finds leads) and
    outreach_account_id (who sends connects/messages).
    """
    __tablename__ = "linkedin_accounts"

    id = Column(String, primary_key=True, default=lambda: str(__import__("uuid").uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String, nullable=False)  # e.g. "Vishnu Saran"
    linkedin_profile_url = Column(String, nullable=True)

    # PhantomBuster configuration — each field is a PB agent/phantom ID.
    # NULL = this account doesn't have this capability.
    pb_api_key_encrypted = Column(Text, nullable=True)  # Fernet-encrypted; NULL = use global PHANTOMBUSTER_API_KEY
    sales_nav_agent_id = Column(String, nullable=True)   # Sales Nav Search Export phantom
    connect_agent_id = Column(String, nullable=True)     # LinkedIn Connect phantom
    message_agent_id = Column(String, nullable=True)     # LinkedIn Message Sender phantom
    inbox_scraper_agent_id = Column(String, nullable=True)  # LinkedIn Inbox Scraper phantom

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


# ── Settings & Suppression ───────────────────────────────────────────────────

class AppSetting(Base):
    """Simple key-value store for app-wide and per-user settings.
    Examples of keys: 'writing_style', 'calendly_link', 'custom_sequence_config',
    '__user__{uid}__linkedin_connect_daily_cap'. Per-user keys use the
    __user__{uid}__ prefix convention."""
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class UnsubscribeEntry(Base):
    """Suppression list. Emails here are never enrolled in future campaigns.

    FIX-32: ``user_id`` is nullable. NULL = global entry (back-compat with
    pre-multi-user data); a non-null value scopes the suppression to that user
    so it doesn't bleed across reps' prospect tables.
    """
    __tablename__ = "unsubscribes"

    email = Column(String, primary_key=True)
    reason = Column(String, nullable=True)      # "manual" | "reply" | "bounce" | etc.
    source = Column(String, nullable=True)      # campaign_id or "manual"
    user_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class ExcludedDomain(Base):
    """
    Domain exclusion list  -  competitors, customers, partners, internal domains.
    Any prospect whose email domain matches is blocked at search/import time.
    """
    __tablename__ = "excluded_domains"

    domain = Column(String, primary_key=True)           # e.g. "competitor.com"
    category = Column(String, nullable=False)            # "competitor" | "customer" | "partner" | "internal"
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class ReplyStatus(str, enum.Enum):
    new = "new"
    meeting_booked = "meeting_booked"
    snoozed = "snoozed"
    archived = "archived"


class Reply(Base):
    """Stores classified reply messages for the reply management dashboard."""
    __tablename__ = "replies"

    id = Column(String, primary_key=True)
    prospect_id = Column(String, ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False, index=True)
    campaign_id = Column(String, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True)
    # SWAT-H12: added index for FK
    sequence_id = Column(String, ForeignKey("email_sequences.id", ondelete="SET NULL"), nullable=True, index=True)

    subject = Column(Text, nullable=True)
    body = Column(Text, nullable=False)
    from_email = Column(String, nullable=True)

    classification = Column(String, nullable=False, index=True)  # positive/negative/ooo/unsubscribe/bounce/neutral
    status = Column(Enum(ReplyStatus), default=ReplyStatus.new, index=True)

    snoozed_until = Column(DateTime, nullable=True)

    received_at = Column(DateTime, nullable=False)
    classified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    prospect = relationship("Prospect")
    campaign = relationship("Campaign")
    sequence = relationship("EmailSequence")


# ── LinkedIn Queue ───────────────────────────────────────────────────────────
# The LinkedIn outreach pipeline is asynchronous:
#   1. Orchestration scheduler decides a prospect needs a connect/message
#   2. Handler writes a LinkedInQueue row (status=queued)
#   3. LinkedIn worker thread picks up queued rows, batches them, and
#      launches a PhantomBuster Phantom
#   4. Worker polls for Phantom results, matches them back to queue rows
#   5. Queue rows are marked done/failed based on PB result classification
#   6. Orchestration state machine advances to the next step

class LinkedInQueueStatus(str, enum.Enum):
    queued = "queued"           # waiting to be picked up by the linkedin_worker
    processing = "processing"   # Phantom launched, waiting for results
    done = "done"               # action confirmed successful (or neutral — already connected)
    failed = "failed"           # action failed (PB error, rate limit, etc.)
    skipped = "skipped"         # skipped (e.g. suppressed, no LinkedIn URL)


class LinkedInQueue(Base):
    """Batch queue for LinkedIn actions. The orchestration scheduler writes rows
    here; the linkedin_worker daemon thread drains them by launching PhantomBuster
    Phantoms and polling for results. Each row represents one action for one prospect."""
    __tablename__ = "linkedin_queue"

    id = Column(String, primary_key=True)
    prospect_id = Column(String, ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False, index=True)
    # SWAT-H12: added index for FK
    campaign_id = Column(String, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True)
    # P3-1 / deep audit CRITICAL-3: multi-tenant scoping column. Nullable so
    # alembic 0003 can add it on prod without rewriting the table, then
    # backfills from Campaign.user_id (preferred) or Prospect.user_id.
    user_id = Column(String, nullable=True, index=True)
    # Multi-account LinkedIn: which LinkedIn account executes this action.
    # NULL = use the default/legacy global PhantomBuster configuration.
    linkedin_account_id = Column(String, ForeignKey("linkedin_accounts.id", ondelete="SET NULL"), nullable=True)
    action_type = Column(String, nullable=False)  # "connect" or "message"
    linkedin_url = Column(String, nullable=False)
    message_text = Column(Text, nullable=True)     # connection note or message body
    status = Column(Enum(LinkedInQueueStatus), default=LinkedInQueueStatus.queued, index=True)
    phantom_container_id = Column(String, nullable=True)
    result_detail = Column(Text, nullable=True)    # per-profile result from PhantomBuster
    created_at = Column(DateTime, default=_utcnow)
    processed_at = Column(DateTime, nullable=True)

    prospect = relationship("Prospect")
    campaign = relationship("Campaign")


# ── Newsletter System ────────────────────────────────────────────────────────
# Newsletters are bulk email sends to prospect or client lists via Microsoft
# Graph API (Outlook). They have their own suppression list (separate from
# campaign unsubscribes) and per-recipient tracking (sent/opened/clicked).

class NewsletterTrack(str, enum.Enum):
    """Newsletter audience track — determines which recipient list to use."""
    prospect = "prospect"   # outreach prospects (from the prospects table)
    client = "client"       # existing clients (manually managed list)
    hubspot = "hubspot"     # auto-synced from HubSpot CRM contacts


class NewsletterStatus(str, enum.Enum):
    draft = "draft"
    scheduled = "scheduled"
    sending = "sending"
    sent = "sent"
    failed = "failed"


class NewsletterRecipientStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    delivered = "delivered"
    opened = "opened"
    clicked = "clicked"
    bounced = "bounced"
    failed = "failed"
    unsubscribed = "unsubscribed"


class Newsletter(Base):
    """Newsletter issue for prospect or client tracks."""
    __tablename__ = "newsletters"

    id = Column(String, primary_key=True, default=lambda: str(__import__("uuid").uuid4()))
    title = Column(String, nullable=False)
    track = Column(Enum(NewsletterTrack), nullable=False, index=True)
    status = Column(Enum(NewsletterStatus), default=NewsletterStatus.draft, index=True)
    owner_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    subject_line = Column(Text, nullable=True)
    preheader_text = Column(Text, nullable=True)
    content_json = Column(JSON, default=dict)
    html_rendered = Column(Text, nullable=True)
    scheduled_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    hubspot_last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    owner = relationship("User")
    recipients = relationship("NewsletterRecipient", back_populates="newsletter", cascade="all, delete-orphan")

    @property
    def is_sendable(self) -> bool:
        """True when the newsletter has the minimum fields needed for dispatch.

        Only draft or scheduled newsletters are considered sendable.
        Requires subject and owner. Content blocks are optional (body text
        stored in content_json as plain text is sufficient).
        """
        has_subject = bool(self.subject_line)
        has_owner = bool(self.owner_id)
        allowed_status = self.status in (NewsletterStatus.draft, NewsletterStatus.scheduled)
        return has_subject and has_owner and allowed_status

    @property
    def recipient_count(self) -> int:
        return len(self.recipients)


class NewsletterRecipient(Base):
    """Individual recipient row for a newsletter send."""
    __tablename__ = "newsletter_recipients"

    id = Column(String, primary_key=True, default=lambda: str(__import__("uuid").uuid4()))
    newsletter_id = Column(String, ForeignKey("newsletters.id", ondelete="CASCADE"), nullable=False, index=True)
    contact_email = Column(String, nullable=False, index=True)
    contact_name = Column(String, nullable=True)
    contact_company = Column(String, nullable=True)
    source = Column(String, default="manual")
    status = Column(Enum(NewsletterRecipientStatus), default=NewsletterRecipientStatus.pending, index=True)
    sent_at = Column(DateTime, nullable=True)
    opened_at = Column(DateTime, nullable=True)
    clicked_at = Column(DateTime, nullable=True)

    newsletter = relationship("Newsletter", back_populates="recipients")

    __table_args__ = (
        Index("ix_nl_recip_newsletter_email", "newsletter_id", "contact_email", unique=True),
    )


class NewsletterUnsubscribe(Base):
    """Newsletter-specific suppression list, separate from campaign unsubscribes."""
    __tablename__ = "newsletter_unsubscribes"

    id = Column(String, primary_key=True, default=lambda: str(__import__("uuid").uuid4()))
    email = Column(String, nullable=False, unique=True, index=True)
    unsubscribed_at = Column(DateTime, default=_utcnow)
    newsletter_id = Column(String, ForeignKey("newsletters.id", ondelete="SET NULL"), nullable=True)
    reason = Column(Text, nullable=True)

    @classmethod
    def is_suppressed(cls, db, email: str) -> bool:
        """Return True if the email is on the newsletter suppression list."""
        return db.query(cls).filter(cls.email == email).first() is not None


# ── Analytics & Monitoring ────────────────────────────────────────────────────

class DomainHealthSnapshot(Base):
    """Point-in-time snapshot of sending domain health metrics (bounce rate).
    Used to monitor email deliverability and trigger alerts if bounce rate spikes."""
    __tablename__ = "domain_health_snapshots"

    id = Column(String, primary_key=True)
    domain = Column(String, nullable=False, index=True)
    bounce_rate = Column(Float, default=0.0)
    total_sent = Column(Integer, default=0)
    total_bounced = Column(Integer, default=0)
    recorded_at = Column(DateTime, default=_utcnow)


class GtmManualEntry(Base):
    """Manual GTM metric entries (meetings, referrals, inbound leads) for the
    unified GTM Analytics dashboard. Also stores auto-detected inbound leads
    from the sales@ inbox poller."""
    __tablename__ = "gtm_manual_entries"

    id = Column(String, primary_key=True, default=lambda: str(__import__("uuid").uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    category = Column(String, nullable=False)       # linkedin | cold_email | inbound | conference | referral
    metric_key = Column(String, nullable=False)      # meetings_booked | inbound_lead | referral_meetings | etc.
    month = Column(String, nullable=False)           # YYYY-MM
    value = Column(Integer, nullable=False, default=0)
    note = Column(Text, nullable=True)
    source_ref = Column(String, nullable=True)       # conference name, sender email, campaign_id, etc.
    created_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index("ix_gtm_manual_cat_month", "category", "month"),
        Index("ix_gtm_manual_user_month", "user_id", "month"),
    )


# ── Follow-ups Machine ───────────────────────────────────────────────────────

class FollowupDeal(Base):
    """Cached HubSpot deal for the follow-ups machine. The followups_poller
    background thread syncs deals from HubSpot, tracks days since last activity,
    and creates Trello cards when deals go stale so reps get actionable reminders."""
    __tablename__ = "followup_deals"

    id = Column(String, primary_key=True, default=lambda: str(__import__("uuid").uuid4()))
    hubspot_deal_id = Column(String, nullable=False, unique=True, index=True)
    deal_name = Column(String, nullable=True)
    deal_stage = Column(String, nullable=True)
    deal_stage_id = Column(String, nullable=True)
    pipeline_id = Column(String, nullable=True)
    amount = Column(Float, nullable=True)
    owner_name = Column(String, nullable=True)
    owner_email = Column(String, nullable=True)
    hubspot_owner_id = Column(String, nullable=True)
    company_name = Column(String, nullable=True)
    contact_name = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)
    last_activity_at = Column(DateTime, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    days_since_activity = Column(Integer, nullable=True)
    is_overdue = Column(Boolean, default=False, nullable=False, server_default="false")
    trello_card_id = Column(String, nullable=True)
    trello_card_url = Column(String, nullable=True)
    trello_card_created_at = Column(DateTime, nullable=True)
    snoozed_until = Column(DateTime, nullable=True)
    suggested_next_step = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
