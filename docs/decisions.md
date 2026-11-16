# Architecture decision log

Short ADR-style records. Newest at the bottom. Each entry: context, decision, consequences.

---

## ADR-001: Technology stack

- **Date:** 2026-11-16
- **Status:** Accepted

### Context
Recourse manages a customer's dispute for weeks: it waits for bank response windows, receives
the bank's reply, and may escalate to the RBI Ombudsman. It must:
- produce money amounts and deadlines that are exactly right and cite their source,
- survive restarts over long periods,
- keep a human in the loop before anything leaves the system,
- be auditable and measurable.

Model access is limited to the Anthropic and OpenAI APIs; other infrastructure must be free
without a payment card.

### Decision

| Concern | Choice | Why |
|---|---|---|
| Durable case lifecycle | **Temporal (self-hosted)**, Python SDK | Durable timers, signals and queries; workflow history doubles as an audit trail; time-skipping test environment. Managed Temporal needs a card, so self-host. |
| Agent reasoning | **LangGraph**, invoked from Temporal activities | Explicit graphs make mandatory steps (e.g. draft verification) structural, not optional. Short-lived runs fit inside activities. |
| Money and dates | **Deterministic Python rule engine + versioned YAML rulebook** | LLMs must never compute amounts or deadlines. Rules are versioned by effective date and every output carries citations. Fully unit-testable. |
| Drafting / reasoning model | **Claude Sonnet** (`ANTHROPIC_MAIN_MODEL`) | Strong long-form drafting and tool use. |
| Extraction (screenshots, PDFs) | **Claude Haiku** (`ANTHROPIC_EXTRACT_MODEL`) | Native vision and PDF input, structured output, low cost. |
| Evaluation judge | **OpenAI model** (`OPENAI_JUDGE_MODEL`) | Different model family from the drafter reduces self-preference bias. |
| Embeddings | OpenAI embedding model (`OPENAI_EMBED_MODEL`) | Regulation text retrieval only. |
| API | **FastAPI + Pydantic v2**, Python 3.12, **uv** | Typed request/response models; uv gives fast, locked installs. |
| Database | **Postgres + pgvector** (Neon free tier) | Relational case data and vector search in one store. |
| Frontend | **Next.js** (App Router, TypeScript) | Simple to build and deploy. |
| Tracing | **LangSmith** (free plan) | LLM call traces alongside Temporal history and an audit table. |
| CI | **GitHub Actions** | Lint, type-check and tests on every push; no API keys needed. |

Model IDs and addresses come from configuration (`recourse/config.py`), never from business
logic.

### Consequences
- Workflow code must stay deterministic: no LLM/network calls, randomness or wall-clock reads
  inside workflows. All side effects go in activities.
- All timer durations go through `recourse.temporal.timeutil.days()`, so demo mode can shrink
  days to seconds without touching workflow logic.
- Self-hosting Temporal leaves the production hosting choice open (tracked separately).
- Two model providers mean two API keys, but tests use fake LLMs and need neither.
