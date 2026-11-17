# Architecture

> Stub. Filled in as components are built. See [decisions.md](decisions.md) for the reasoning
> behind each choice.

## Components

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
Neon Postgres (+pgvector): cases, evidence, entitlements, drafts, audit_events, reg_chunks
```

## Layers and ownership
- **Temporal (outer, durable):** one `CaseWorkflow` per case owns the state machine, timers,
  signals and queries.
- **LangGraph (inner, short-lived):** intake, drafting (with a mandatory verify step) and
  bank-reply judging, each run inside a Temporal activity.
- **Rule engine:** the only source of money amounts, deadlines and citations.

## Implemented so far
- `GET /health`: API status and Temporal reachability (`backend/recourse/api/health.py`).
- `HelloWorkflow` + `say_hello` activity: a smoke test of the worker setup
  (`backend/recourse/temporal/`).
- `days(n, scale)`: the single helper for timer durations, with demo-mode scaling
  (`backend/recourse/temporal/timeutil.py`).

## To be documented
- Case state machine, signals, queries and timers
- Graph designs (intake, draft + verify, reply judge)
- Rulebook format and regime selection
- Data model and audit trail
- Security controls (PII masking, injection and phishing defences)
