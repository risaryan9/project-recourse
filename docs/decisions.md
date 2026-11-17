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

---

## ADR-002: Rule engine and rulebook design

- **Date:** 2026-11-16
- **Status:** Accepted

### Context
Money amounts and deadlines must be exactly right and traceable to the RBI text. The rules come
from five primary sources (see `docs/sources/SOURCES.md`), some of which change with the
transaction date, and several points in them are open to interpretation.

### Decision
1. **Rules are data.** `backend/recourse/rules/rulebook/*.yaml` holds every number (TATs, ₹/day,
   liability windows and caps, compensation limits, Ombudsman windows). Strict Pydantic models
   (`extra="forbid"`) parse it; a broken rulebook fails at load time and in CI.
2. **Every value carries exact quotes from its source.** `tests/rules/test_rulebook.py` extracts
   the text of the local PDF and fails if any quote is missing (whitespace is ignored because PDF
   extraction inserts stray spaces). Golden tests then check the numbers themselves.
3. **`verified` flags start as `false`.** A human sets one to `true` only after checking that rule
   against the PDF line by line. Unverified rules raise a `RULE_NOT_VERIFIED` flag on every result.
4. **The engine is pure.** `assess_failed_transaction`, `assess_fraud` and `assess_escalation` take
   an explicit `as_of` date and never read the clock, network or an LLM (enforced by a test).
5. **Regime by transaction date (and bank type) for fraud:**
   - `FRAUD-A-2017` (6 Jul 2017 – 27 Nov 2025): 2017 circular. Covers scheduled commercial banks,
     RRBs, Small Finance Banks and Payments Banks (its addressees).
   - `FRAUD-A-2025` (28 Nov 2025 – 31 Dec 2026): RBC Directions 2025, Section D. Same rules,
     renumbered; commercial banks only.
   - `FRAUD-B-2027` (from 1 Jan 2027): Third Amendment Directions 2026, Section DA; commercial
     banks only.
6. **Fail closed for bank types we have not read the rules for** (Small Finance Banks, Payments
   Banks, RRBs and Local Area Banks after 27 Nov 2025; co-operative banks; NBFCs): the engine
   returns `BANK_TYPE_NOT_IN_RULEBOOK` with no liability figures, but still shows the urgent
   1930 / cybercrime.gov.in guidance.
7. **Day-counting conventions:**
   - "Within N days of D" excludes D (General Clauses Act, 1897, s.9): the last day is D + N.
   - Delay compensation = (reversal date, or today) − (T + TAT) calendar days, floored at 0.
   - Regime A working days are approximated: Sundays, 2nd/4th Saturdays and 26 Jan, 15 Aug,
     2 Oct are non-working. Results are flagged `WORKING_DAYS_APPROXIMATE`, and a count within one
     day of a liability bucket edge is flagged `BORDERLINE_WORKING_DAYS` ("confirm with your
     branch").
   - Regime B small-value compensation applies to frauds occurring 1 Jan 2027 – 31 Dec 2027
     inclusive ("up to one year from the effective date", para 76U).
   - Money is `Decimal`; 85% of net loss is rounded half-up to the paisa.
8. **Credit-push payee ambiguity.** Annex principle 1(a) of the 2019 circular says the penalty for
   a credit-push transfer that is not credited "has to be paid to the beneficiary", while the
   table rows describe reversal to the remitter. We show the ₹100/day as owed to the customer
   and raise `PAYEE_AMBIGUOUS` citing principle 1(a). Open question until clarified.
9. **Ombudsman waiting period** is the higher of 30 days and the regulatory reply time for the
   case (90 days for Regime A fraud, 45/60 for Regime B), per RB-IOS 2026 clause 10(1)(f). The
   fraud `Entitlement` hands its reply time and citation to `assess_escalation`.

### Consequences
- Adding a rule means adding YAML with quotes, plus golden cases; no engine change for a new row.
- Regime A working-day results can be off by a day for customers whose branch has state
  holidays; the borderline flag makes this visible instead of hiding it.
- Fraud cases at non-commercial banks get guidance but no numbers until their directions are
  added to the rulebook.

---

## ADR-003: Evidence model and intake validation

- **Date:** 2026-11-17
- **Status:** Accepted

### Context
The extractor (an LLM reading screenshots, SMS and PDFs) can misread or invent values. The rule
engine's numbers are only as correct as the facts it is given, and uploads can contain secrets
(OTP, PIN, full card numbers) that must never be stored.

### Decision
- `recourse/evidence/models.py`: one `Evidence` object per upload. Every fact is an
  `Extracted[T]` holding `value`, `confidence` (0-1), `source_text` (the verbatim snippet it was
  read from) and `origin` (`EXTRACTED` or `USER`). A fact that is not found is `value=None`,
  never a guess. User-confirmed values carry confidence 1.0.
- **Two layers of checks.**
  - *Hard* (Pydantic, at construction): types, enums, amount parsing (`Decimal` rupees,
    "₹1,250.00" style accepted), card/account numbers cut to the last 4 digits, and PII redaction
    (`recourse/security/pii.py`) of every string before the object exists.
  - *Soft* (`recourse/evidence/validators.py`, `check_evidence`): UTR is 12 digits (required for
    UPI/IMPS), amount > 0, no future dates, reversal/alert dates consistent with the transaction,
    and minimum confidence (default 0.85). These return issues instead of raising, so a misread
    becomes a question to the user (`needs_user_confirmation`) rather than a failed upload.
- **Time zone:** timestamps without a zone are taken as IST, and the rule engine's day `T` is
  the IST calendar date.
- `now` is always passed in explicitly, so checks are deterministic and replay-safe.
- `case_type` and `scenario` (the extractor's reading of what went wrong) live on `Evidence` and
  are confirmed like any other field before the rule engine runs.

### Consequences
- Redaction is pattern-based and conservative: it may over-redact (e.g. a postal PIN code after
  "PIN"), and a bare 12-digit number is never treated as Aadhaar because UTRs are also 12 digits.
- Facts the documents do not show (fault, reporting dates, bank type) are asked from the user
  later; merging several uploads into one case is a separate step.

---

## ADR-004: LLM client layer

- **Date:** 2026-11-17
- **Status:** Accepted

### Context
Several parts of Recourse call a model (extraction, reply classification, drafting, reply
judging). Tests and CI must run without network access or API keys, model IDs must come from
configuration, and every prompt must treat uploaded and forwarded content as untrusted.

### Decision
- One interface, `LLMClient` (`recourse/llm/base.py`), with two async methods:
  `structured(request, schema)` returns a validated Pydantic object, and `text(request)` returns
  free text. Business code receives a client and never constructs one (dependency injection).
- Implementations: `AnthropicLLM` (real API) and `FakeLLM` (scripted answers per call
  `purpose`, recording every rendered prompt).
- Requests name a **role** (`MAIN` or `EXTRACT`), mapped to `ANTHROPIC_MAIN_MODEL` /
  `ANTHROPIC_EXTRACT_MODEL`.
- Inputs are split by trust. `system` and `task` are ours; `documents` and `attachments` are
  untrusted. `recourse/llm/prompting.py` (shared by both clients) wraps documents in
  `<untrusted_document>` tags, neutralises any tag-like text inside them, redacts PII, places our
  task after the data, and prefixes a fixed safety preamble to the system prompt.
- The system prompt carries a prompt-cache breakpoint and contains no per-call data.
- Structured output uses `output_config.format` with `anthropic.transform_schema`. We check
  `stop_reason` (refusal, truncation) before validating the JSON ourselves.
- Errors map to `LLMUnavailableError` (retryable: rate limits, 5xx, network),
  `LLMRejectedError`, `LLMRefusalError` and `LLMOutputError` (not retryable), so Temporal
  retry policies can use the `retryable` flag.
- `allow_fallback=True` enables the server-side refusal fallback for the main model.
- Logs record purpose, model, stop reason, token usage and request ID, never content.

### Consequences
- Images and PDFs cannot be redacted before they are sent; their extracted output is redacted by
  the `Evidence` model.
- The real client is tested through a mock HTTP transport (real SDK, no network). Model quality
  is measured separately by the eval suites.

## ADR-005: OpenAI as a fallback LLM provider

### Context
A single provider is a single point of failure: an outage, rate limit or key problem on the
Anthropic side would stall every case. The stack already allows OpenAI APIs.

### Decision
- `OpenAILLM` (`recourse/llm/openai_client.py`) implements `LLMClient` on the OpenAI Responses
  API. It uses the same `render_prompt` output (redaction, untrusted-data wrapping, safety
  preamble, task last), translated to Responses input parts, so both providers get the same
  prompt and the same safety rules.
- Role mapping: `MAIN` → `OPENAI_MAIN_MODEL` (default `gpt-6.1-sol`), `EXTRACT` →
  `OPENAI_EXTRACT_MODEL` (default `gpt-6.1-terra`, positioned by OpenAI for visual reasoning).
- Structured output uses a strict JSON schema; refusals, content-filter stops and truncation are
  checked before validation, like the Anthropic client. Requests are sent with `store=False`.
- `FallbackLLM` (`recourse/llm/fallback.py`) tries providers in `LLM_PROVIDERS` order
  (default Anthropic, then OpenAI), skipping any provider without an API key. It falls back only
  on `LLMUnavailableError` and `LLMRejectedError`. It does **not** fall back on refusals (a
  safety decision is not shopped around) or on output errors.
- If all providers fail and any failure was retryable, the result is `LLMUnavailableError`, so
  Temporal retries later.

### Consequences
- Redacted case text may be sent to OpenAI when the fallback runs; both providers are allowed by
  the stack, and the same redaction applies.
- `LLMResult.served_model` records which provider's model answered; drafts and evals should keep
  it. When OpenAI drafted, the OpenAI judge is no longer a different model family, so eval runs
  should use the primary provider.
- Anthropic's server-side refusal fallback (`allow_fallback`) has no OpenAI equivalent and is
  ignored by `OpenAILLM`.
