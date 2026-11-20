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

## ADR-006: IntakeGraph

### Context
Intake turns a user's submission (typed/pasted text, screenshots, PDFs) into `Evidence` the rule
engine can trust. Models misread blurry images and ambiguous text, and submissions may contain
prompt-injection attempts or secrets.

### Decision
- A LangGraph graph (`recourse/agents/intake_graph.py`): `classify` → (`UNRELATED` → stop) →
  `extract` → `validate`. Both LLM steps use the `EXTRACT` role through an injected `LLMClient`.
  The whole submission is passed as untrusted documents/attachments.
- The model fills a simpler **extraction form** (`intake_schema.ExtractionOutput`: one
  `Fact[T]` of value/confidence/source_text per `Evidence` fact; every field required and
  nullable so strict structured output works on both providers). Code converts it to `Evidence`.
- A value that fails `Evidence`'s hard checks (unparseable amount or date, too-short card
  number) is **dropped to missing** with a "Could not read …" note. It is never repaired by
  guessing. `check_evidence` then lists what the user must confirm.
- Result statuses: `READY`, `NEEDS_CONFIRMATION`, `NOT_PAYMENT_EVIDENCE`.
- `apply_corrections(evidence, {field: value}, now=…)` applies the user's answers (origin
  `USER`, confidence 1.0) and re-runs the checks with no LLM call.
- `now` is an input (IST date passed to the model to resolve "yesterday" or a missing year), so
  runs are reproducible and safe inside a Temporal activity. LLM errors propagate for the
  activity's retry policy.
- Sensitive data found in the typed text (removed before any model saw it) is recorded in
  `Evidence.redactions`, so the UI can warn the user.

### Consequences
- Two EXTRACT-model calls per submission (classification is cheap and stops unrelated input
  before extraction).
- One submission produces one `Evidence`. Merging several uploads for one case is a later step.
- Secrets inside images/PDFs cannot be removed before the model sees them; they are removed from
  the extracted output by `Evidence`.

## ADR-007: Neon Postgres from day one

### Context
Case data (cases, evidence, drafts, the audit log) needs a durable store before the
CaseWorkflow and API are built. The options were a local Postgres for development with Neon
only at deployment, or Neon everywhere.

### Decision
- Use **Neon Postgres** (free plan, no card required) for development as well as deployment.
  No local Postgres container.
- One Neon project with three branches:
  - `main`: the deployed instance and demo data;
  - `dev`: local development (`DATABASE_URL`);
  - `test`: database tests (`TEST_DATABASE_URL`). Tests that need it are skipped when it is
    unset, so CI and unit tests still run with no database.
- Use the **direct** (non-pooled) connection string with `sslmode=require`. The app has few
  connections, and the direct endpoint avoids the PgBouncer transaction-mode limits on prepared
  statements and on migrations.
- Driver: SQLAlchemy 2 + psycopg 3 (`postgresql+psycopg://`). Schema changes go through Alembic
  only. The first migration enables `pgvector` (`CREATE EXTENSION IF NOT EXISTS vector`).
- Only synthetic data is stored, as everywhere in the project.
- Project created with the Neon CLI (`neon init`, `--no-config`: no `neon.ts` or Node
  dependencies in this Python repo). Region `aws-ap-southeast-1` (Singapore, the closest region
  to India), Postgres 18. The local `.neon` link file is git-ignored.

### Consequences
- Neon terminates TLS at its proxy, so `pg_stat_ssl` on the server shows `ssl = false`; the
  client side is encrypted (`sslmode=require`, verified with psycopg's `ssl_in_use`).
- Development needs internet access, and the free compute auto-suspends when idle, so the first
  query after a pause takes a moment (cold start).
- The free plan's storage and compute-hour limits are ample for text case data; check them in the
  Neon console if usage grows.
- Branches make it cheap to reset `dev`/`test` from `main` or to throw them away.

## ADR-008: Database schema v1 (cases, evidence, audit log)

### Context
The CaseWorkflow and API need somewhere to keep cases, the facts extracted for them, and a
record of every step (CLAUDE.md quality bar: auditable).

### Decision
- SQLAlchemy 2 models (`recourse/db/models.py`), async engine with psycopg 3
  (`sqlalchemy[asyncio]`, which brings `greenlet`). Alembic migrations in `backend/migrations/`,
  hand-reviewed; the URL comes from settings, never from `alembic.ini`.
- Three tables:
  - `cases`: UUID id, unique short `case_tag` (`RC-XXXX`, for email subjects), `status`
    (the CaseWorkflow states), timestamps.
  - `evidence`: one row per accepted submission. Stores the `Evidence` object and the
    validator result as JSONB, plus `schema_version`. **Raw uploads are not stored**, only the
    redacted, structured facts.
  - `audit_events`: append-only (a trigger rejects `UPDATE`); `actor`, dotted `event_type`,
    JSONB `details` that are PII-redacted before insert (`record_event`).
- Enums are stored as `VARCHAR` without database enum types or CHECK constraints, so adding a
  workflow state needs no migration; Python validates values.
- `ON DELETE CASCADE` from `cases`, so deleting a case removes all its data.
- The first migration enables `pgvector`; the `reg_chunks` table comes with regulation search,
  once the embedding model (and so the vector size) is chosen.
- Database tests run against the Neon `test` branch and are opt-in (`pytest -m db`); a test
  fails if the models and the migrations drift apart.

### Consequences
- The default test run stays offline. Run `pytest -m db` after any model or migration change.
- Case status is stored in two places (Temporal and `cases.status`); Temporal is the source of
  truth, and the workflow updates the table through an activity.

## ADR-009: CaseWorkflow v1 (intake → review → assessment) and the case API

### Context
The first part of a case: from a submission to the rule engine's answer, with the user
reviewing the extracted facts in between. It must survive restarts, retry failed steps, keep
raw uploads out of durable history, and leave an audit trail.

### Decision
- **One `CaseWorkflow` per case** (`recourse/temporal/case_workflow.py`), workflow ID
  `case-<uuid>`. Steps: `case.run_intake` → wait for `confirm` signals (re-checked by
  `case.confirm` until nothing is missing) → `case.assess` → ASSESSED → wait (drafting plugs in
  next). An `abandon` signal ends it from any waiting point. Query: `get_state`.
- **Activities do the work and own the writes** (`case_activities.py`): each writes its result,
  the new `cases.status` and an audit event in one transaction, and is idempotent so Temporal can
  retry it. Bad user input and permanent LLM failures raise non-retryable `ApplicationError`s,
  which the workflow handles (keep waiting with `last_error`, or end the case).
- **The user always reviews the facts once**, even with no issues (the "editable fields →
  Assess" screen).
- **Raw uploads never enter Temporal history.** `POST /cases` stores the submission in
  temporary `uploads` / `upload_files` rows (text PII-redacted first; images/PDFs as-is) and
  passes only IDs to the workflow. The intake activity deletes them in the same transaction that
  stores the evidence.
- **Fraud cases ask the user directly** for facts no evidence shows (`CaseDetails`: bank type,
  required; account type, fault, reporting dates, optional), stored in `cases.details`.
- **Temporal payloads use the Pydantic data converter** (`temporalio.contrib.pydantic`);
  worker and API share `case_runner.connect()`.
- **API** (`recourse/api/cases.py`): `POST /cases` (202), `GET /cases/{id}`,
  `POST /cases/{id}/confirm` (validated immediately with the same `review()` the activity uses,
  then signalled), `POST /cases/{id}/abandon`. Pages read the database, so they work even when
  Temporal is down; the workflow query adds `last_error`.
- If the workflow cannot be started, `POST /cases` deletes the case again and returns 503.

### Consequences
- Case status lives in Temporal and in `cases.status`; the activities keep them in step.
- **No authentication yet**: anyone with a case ID can read it. Accounts are required before
  deployment.
- Changing the workflow code later must keep replay compatibility for running cases (use
  `workflow.patched()` or a new workflow type).
- Images/PDFs sit unredacted in Postgres for the seconds between upload and intake.

## ADR-010: DraftGraph with placeholder templates and a mandatory verify step

### Context
The bank complaint must quote amounts, dates and RBI clauses that exactly match the rule
engine (CLAUDE.md quality bar: correct numbers, grounded). LLMs copy and transform numbers
unreliably, and a wrong amount in a letter to a bank is worse than no letter.

### Decision
- **The LLM never writes a value.** It returns a template (`subject`, `body`) with
  `{{slot}}` placeholders. `agents/draft_slots.py` builds the slot table from the rule engine's
  `Entitlement` (money, deadlines, durations, citations) and the user-confirmed `Evidence`
  (amount, date, UTR, bank and payee names), and `render` fills them in. Indian formatting
  (`₹1,25,000.00`, `19 November 2026`). A citation names its document in full the first time
  and as "para X of <ref>" after that.
- **`verify` is a graph node on the only path to a draft** (`agents/draft_verify.py`, pure
  Python). It rejects: unknown or malformed placeholders; missing required ones (every money
  line and the total, the key deadline, the citations behind them, the transaction reference,
  `{{case.tag}}` in the subject, the sign-off name); any digit, currency marker, number word
  next to a unit, or web address typed outside a placeholder; mentions of OTP/PIN/CVV/password
  or remote-access apps; mentions of Recourse; bad length. After rendering, every ₹ amount must
  be one of the case's amounts (an independent second check).
- **Revise and fail closed.** On failure the exact error list goes back to the model; at most
  3 attempts in total, then status `FAILED` with no letter text.
- **No untrusted text in the drafting prompt.** The model sees placeholder names and
  descriptions, the case type and rule-engine flags, never evidence values. Payee names, bank
  names and references (which come from uploads) only enter the letter through `render`.
- **Written as the customer**, first person, with a `[Your full name]` placeholder the user
  fills in; Recourse is not mentioned in the letter. The "informational, not legal advice"
  notice travels with the draft (`DraftResult.disclaimer`) and is shown next to it in the UI,
  not inside the email to the bank.
- **Unverified rules:** drafts are produced and carry `rules_verified`; the email channel must
  refuse a real (non-sandbox) send until every cited rule is verified. Sandbox sends are allowed.
- Scope: bank complaints only. The 1930/cybercrime report and the Ombudsman complaint reuse the
  graph later with their own required placeholders.

### Consequences
- A number the model needs but has no placeholder for is left out of the letter; add a slot
  rather than relaxing the checker.
- The no-digits rule is strict, so models sometimes need a second attempt (seen in a real run);
  three attempts leave room for that.
- The checker is deterministic and unit-tested, including adversarial templates.

## ADR-011: Drafting and approval in the CaseWorkflow

### Context
After assessment, the case needs a bank complaint the user approves before anything is sent
(CLAUDE.md §3 and §5: per-email approval). The user may want to reword the letter, and must add
their name, but the amounts and dates must stay the rule engine's (ADR-010).

### Decision
- **Flow.** `ASSESSED` → (only if the entitlement is covered) `case.draft` activity →
  `AWAITING_APPROVAL` → `approve_draft` | `reject_draft`. Rejection drafts again with the user's
  reason as feedback. A draft that fails closed leaves the case at `ASSESSED`, and a new
  **`redraft`** signal tries again. At most `MAX_DRAFTS_PER_CASE` = 5 drafts per case, which caps
  the LLM cost. `abandon` works from every wait.
- **`DRAFT_READY` is not a resting state.** The draft activity stores the draft, records
  `draft.ready` and sets `AWAITING_APPROVAL` in one transaction, because nothing happens in
  between. The same pattern will apply to `ESCALATION_DRAFT_READY`.
- **`drafts` table** (migration 0003), one row per attempt, keyed by `(case_id, seq)`. The
  workflow passes `seq`, so a retried draft activity finds its row instead of paying for a second
  LLM run. Statuses: `PENDING`, `APPROVED`, `REJECTED`, `FAILED` (no letter text).
- **No PII in Temporal history.** The user's name, edited text and reject reason are written to
  the draft row by the API (`final_subject`, `final_body`, `reject_reason`; the reason is
  PII-redacted). The signals carry only `draft_id`. The API checks the input first, which gives
  the user an immediate 422. The approve activity checks the stored text again and makes the
  decision.
- **Edits are free text with fixed facts** (`check_final`). The subject must keep the case tag
  (the reply watcher relies on it), and no placeholder may be left. Every amount written with ₹,
  Rs or INR, and every long-form date, must be one of the case's slot values. No credential or
  remote-access wording. The user's name: 2–100 characters, no digits or brackets.
- **Only the current draft can be decided.** Decisions about an older `draft_id` are refused
  (workflow and activity), with the reason in `get_state().last_error`.
- Case status stays `AWAITING_APPROVAL` after approval until the send step exists. The draft row's
  `APPROVED` status and `get_state().approved_draft_id` record the approval.
- **Replay safety:** the new code path is behind `workflow.patched("drafting-v1")`, so cases that
  were already waiting at `ASSESSED` replay on the old path.

### Consequences
- The edit check does not catch every possible change of fact (e.g. a bare number with no
  currency marker, a date written as "17/11/2026", or a changed UTR). It catches the
  consequential ones (amounts, deadlines). The letter is the user's own and they approve it.
- The entitlement is not recomputed between drafting and approval, so accruing compensation is
  as of the assessment date. The send step should reassess when the delay matters.
- Signals are fire-and-forget: the API's own check gives fast feedback, and the activity's refusal
  appears as `last_error`. Temporal Updates could make this synchronous later.

## ADR-012: EmailChannel, the send policy and ManualChannel

### Context
An approved complaint must reach the bank without the agent ever sending something the user did
not approve, or sending to the wrong address (CLAUDE.md §3 "Email channel", §5). ManualChannel
must always work; GmailChannel comes later on the same path.

### Decision
- **One interface, one policy.** `EmailChannel.send(OutgoingEmail) -> SendOutcome` in
  `recourse/email/channel.py`. Every channel uses the same policy first, `resolve_recipient`:
  - sandbox on (the default): the recipient is always `SANDBOX_RECIPIENT`; no address set
    means the send is blocked;
  - sandbox off: the bank's grievance address from the contacts file, only if that entry is
    `verified`, has an email, **and** every cited rule is verified (ADR-010). Otherwise the send
    is blocked with the reasons and the user gets no address.
- **ManualChannel never sends.** It returns a `mailto:` link (RFC 6068 encoding, CRLF line ends).
  The email row is `PREPARED`; the user sends it from their own mail app and confirms with the
  **`mark_sent(channel, sent_at)`** signal. The `record_sent` activity then refuses a time more
  than 10 minutes in the future or before the approval, sets `SENT`, and moves the case to
  `SENT_TO_BANK`. A channel that sends by itself (Gmail) returns `sent=True` and skips
  `mark_sent`.
- **`outbound_emails` table** (migration 0004), one row per approved draft (unique `draft_id`):
  channel, status `BLOCKED | PREPARED | SENT`, sandbox flag, bank, recipient, the approved text,
  blocked reasons, `sent_at`, Gmail `message_id` / `thread_id`. `prepare_send` is idempotent. A
  BLOCKED row is evaluated again on retry, so it changes if the user chose their bank since.
  The workflow then waits for a new **`retry_send`** signal.
- **Bank choice.** `cases.bank_id` is set when the user picks a bank (`PUT /cases/{id}/bank`).
  Otherwise the extracted bank name is matched against the contacts file's aliases (whole
  words, longest alias; two banks matching about equally well = no match).
- **Contacts file** (`recourse/email/bank_contacts.yaml`): 10 banks, researched from official
  pages only (each entry lists its `source_urls`), all `verified: false`. Values not found on
  an official page are `null` with a note; none are filled in from memory. Banks have moved to
  `.bank.in` domains, which are used as the official hosts. No bank states its sending domains,
  so `sender_domains` are the domains of its official contact addresses
  (`sender_domains_basis: contact_emails`). The sender-domain check must treat them as a
  heuristic.
- Case status stays `AWAITING_APPROVAL` while the email is PREPARED (the email row tells the UI
  to show "send it yourself"), as in ADR-011.
- New code path behind `workflow.patched("sending-v1")`.

### Consequences
- With the defaults (sandbox on), a demo never needs verified contacts. Turning sandbox off
  sends nothing until a maintainer has verified the bank's entry and the cited rules.
- SBI, ICICI, Axis, Kotak and Canara have no complaint email on file yet. In real mode their
  cases are blocked, with the bank's online complaint form shown where one is known.
- `prepare_send` calls the channel inside its database transaction. That is fine for
  ManualChannel. GmailChannel must make its send idempotent (e.g. check the thread for the
  case tag before resending), because an activity retry after a successful send would otherwise
  send twice.
