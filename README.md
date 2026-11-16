# Recourse

**Recover money your bank owes you after a digital payment goes wrong.**

When a UPI transfer fails, an ATM doesn't dispense cash, or you're defrauded, Reserve Bank of
India (RBI) rules already give you strong rights: auto-reversal deadlines, ₹100/day
compensation for delays, zero-liability windows for fraud, and RBI Ombudsman awards. Few people
use them, because the rules are scattered across circulars, change over time, and come with
hard deadlines.

Recourse is an agentic assistant that acts as the customer's advocate and manages the case for
weeks until it is resolved:

1. You upload evidence (UPI screenshot, bank SMS, statement PDF) or describe the problem.
2. Recourse extracts the facts: UTR/RRN, amount, date, channel, bank.
3. A **deterministic rule engine** computes what you're owed (refund + compensation) and every
   deadline, **citing the exact RBI clause** for each number.
4. Recourse drafts the complaint to your bank (and a cybercrime/1930 report for fraud).
5. **You approve and send it yourself.** Recourse never sends or files anything.
6. It waits for the bank's response window, judges the bank's reply against the rules, and flags
   phishing attempts.
7. If the bank doesn't comply, it drafts the RBI Ombudsman complaint within the filing window.

> ⚠️ Recourse provides information, not legal advice.

**Status:** early development. The repository currently contains the project skeleton: API
health check, a durable-workflow smoke test, the frontend shell, CI and Docker setup.

## Architecture

```
Next.js frontend
   │  REST
   ▼
FastAPI ──start/signal/query──► Temporal server
   │                                  │
   │                                  ▼
   │                         Temporal worker (Python)
   │                           ├─ CaseWorkflow (one per case, runs for weeks)
   │                           └─ Activities ──► LangGraph graphs ──► Claude / OpenAI
   │                                          └─► Rule engine (pure Python)
   ▼
Postgres (+pgvector)
```

- **Temporal** owns the long-running, durable case lifecycle (timers, signals, restarts).
- **LangGraph** runs short reasoning steps (extraction, drafting, reply judging) inside
  Temporal activities.
- **The rule engine is the only source of money amounts and dates.** LLMs never compute them.

See [docs/architecture.md](docs/architecture.md) and [docs/decisions.md](docs/decisions.md).

## Running locally

### Prerequisites
- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node.js 22+
- [Temporal CLI](https://docs.temporal.io/cli) (or Docker, see below)

### Configuration
```bash
cp .env.example .env   # fill in API keys when needed; the skeleton runs without them
```

### Start the services (separate terminals)
```bash
# 1. Temporal dev server (Web UI at http://localhost:8233)
temporal server start-dev --ui-port 8233

# 2. Backend
cd backend && uv sync
uv run python -m recourse.temporal.worker        # Temporal worker
uv run fastapi dev recourse/api/main.py          # API at http://localhost:8000

# 3. Frontend (http://localhost:3000)
cd frontend && npm install && npm run dev
```

Smoke-test the workflow (it then shows up in the Temporal UI):
```bash
cd backend && uv run python -m recourse.temporal.run_hello Recourse
```

### Everything in Docker
```bash
docker compose -f infra/docker-compose.yml up --build
```
This starts the Temporal dev server, API and worker. The database is external (set
`DATABASE_URL`).

### Tests and checks
```bash
cd backend
uv run pytest -q          # unit + workflow tests; no network or API keys needed
uv run ruff check . && uv run mypy recourse tests

cd frontend && npm run lint && npm run build
```

### Demo mode
With `DEMO_MODE=true`, one "day" lasts `DEMO_DAY_SECONDS` seconds (default 60), so a case that
normally takes weeks can be walked through in minutes.

## Evaluation results

Coming soon: rule-engine golden cases, extraction accuracy, draft quality and adversarial
(prompt-injection / phishing) suites, with results committed under `evals/reports/`.

## Credits

AI APIs:
- [Anthropic Claude API](https://docs.anthropic.com/): drafting and document extraction
- [OpenAI API](https://platform.openai.com/docs): independent evaluation judge and embeddings

Open-source libraries and tools:
- [Temporal](https://temporal.io/) and the [Temporal Python SDK](https://github.com/temporalio/sdk-python)
- [LangGraph](https://github.com/langchain-ai/langgraph)
- [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/),
  [Pydantic](https://docs.pydantic.dev/) and pydantic-settings
- [SQLAlchemy](https://www.sqlalchemy.org/), [Alembic](https://alembic.sqlalchemy.org/),
  [psycopg](https://www.psycopg.org/), [PyYAML](https://pyyaml.org/)
- [Next.js](https://nextjs.org/), [React](https://react.dev/),
  [Tailwind CSS](https://tailwindcss.com/)
- [uv](https://github.com/astral-sh/uv), [Ruff](https://github.com/astral-sh/ruff),
  [mypy](https://mypy-lang.org/), [pytest](https://pytest.org/)

Design inspiration: [ElimentaryLabs/agent-harness-starter](https://github.com/ElimentaryLabs/agent-harness-starter)
(MIT), for the ideas of verification as a guaranteed step, a human permission gate,
observability by default and fake-LLM tests.

## Data

The repository only contains synthetic sample data. It never contains real customer data, bank
details or credentials.
