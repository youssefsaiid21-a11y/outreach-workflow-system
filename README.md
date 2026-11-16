# Outreach Workflow System

A Python/FastAPI application for prospect research, campaign orchestration, draft generation, reply handling and activity reporting. Includes a browser dashboard, a Click CLI, SQLAlchemy persistence, Alembic migrations and provider adapters.

**Status: development portfolio version.** Company branding, private handoff notes, deployment units, customer case-study claims and hard-coded database migration credentials are excluded from this version. The remaining example context is fictional.

## What to review

- `src/orchestration/`: state machine, scheduler, dispatch, handlers and repositories.
- `src/api/`: authenticated application routes and workflow operations.
- `src/services/`: research, drafting, provider integrations and reply processing.
- `src/db/`, `alembic/`: data models, persistence and migrations.
- `dashboard/`: browser interface; `src/cli/`: command-line workflow.
- `tests/`: service, routing, isolation and workflow regression tests.

The stack includes FastAPI, SQLAlchemy, PostgreSQL/SQLite, Pydantic, Anthropic, MSAL, HTTPX and pytest. Adapters for email, CRM, prospecting and LinkedIn services require separately configured development accounts.

## Local review

Use Python 3.11 or 3.12 and an isolated environment:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set your own local authentication/encryption secrets and only the development integrations you intend to exercise. The example uses a local SQLite database; PostgreSQL is supported through `DATABASE_URL`.

```sh
uvicorn main:app --host 127.0.0.1 --port 3004
```

Open <http://localhost:3004>. Application startup can initialize database state and background workers. Use an isolated local environment with no real recipient lists, active campaigns, production provider accounts or deployment service units.

```sh
pytest -q tests
```

Review test configuration before supplying any provider credentials. Live campaigns, real messages and provider API calls are outside the portfolio validation scope. No external outreach is sent during repository preparation.

## Current checks

Python syntax checks passed. The regression run returned **691 passed and 55 failed**. These failures are unresolved; see [validation status](VALIDATION.md) before evaluating deployment readiness.

## Boundaries and limitations

This application can perform external actions when configured. A code review does not establish production readiness, message quality or business outcomes. Review authorization, data retention, suppression rules, provider permissions and operational behavior before deployment. Historical migration utilities with embedded database credentials are removed rather than offered as setup instructions.
