"""
Orchestration package for the Example cold outreach system.

This package implements the WAT (Workflows, Agents, Tools) orchestration layer
that drives multi-step, multi-channel outreach sequences for each lead.

Sub-modules:
    enums        -  All status/type enumerations
    orm          -  SQLAlchemy ORM models for orchestration tables
    models       -  Pydantic v2 data models
    config       -  Sequence configuration and the default Example 30-day sequence
    providers    -  Abstract provider interfaces + concrete implementations
    repositories  -  Database access layer
    state_machine  -  Explicit FSM for lead sequence transitions
    conditions   -  Step condition evaluators
    scorer       -  Lead engagement scoring
    handlers     -  Per-step-type action handlers
    tasks        -  Manual task factory helpers
    scheduler    -  Scheduler loop (find due steps, execute them)
    ingestion    -  Webhook event ingestion and normalisation
    dispatcher   -  Central action dispatcher that routes steps to handlers
    api          -  FastAPI router exposing all orchestration endpoints
    examples     -  Example payloads for testing / documentation
"""
