# CLAUDE.md — Recourse

> Project instructions for Claude Code. Read this fully before planning or writing code.
> Owner: Aryan (solo developer).

---

## 0. Start of every session (mandatory)

1. Read **`context/initial-context.md`** by its explicit path. It holds private planning context: timeline, milestones, priorities, demo plan, and decision history.
   - `context/` is **git-ignored**. Search tools that respect `.gitignore` may skip it, so always open it by path.
2. **Privacy rule for `context/`:** everything in `context/` is private. Never copy, quote, paraphrase, or reference its contents (names of events, organisations, people, deadlines, strategy) in anything that can leave this machine:
   - source code, comments, docstrings, test names, fixtures,
   - README, `docs/`, ADRs, UI text, seed data,
   - commit messages, branch names, PR titles/descriptions, issue text.
   
   If a task seems to need such a reference, stop and ask Aryan.
3. Never `git add -f` anything under `context/`. Before every commit, check `git status` / `git diff --cached` to confirm nothing from `context/` is staged.

---

## 1. What we are building

**Recourse** is an agentic AI that helps Indian bank/UPI customers **recover money they are legally owed** when a digital payment goes wrong. It manages the case over weeks until it is resolved.

When a UPI transfer fails, an ATM doesn't dispense cash, or a customer is defrauded, RBI rules already give them strong rights: auto-reversal deadlines, ₹100/day compensation, zero-liability windows, and Ombudsman awards. Few people use them. The rules are scattered across circulars, changed twice in 2026, and have hard deadlines. Recourse acts as the customer's advocate.

### Core user flow
1. User uploads evidence (UPI screenshot, bank SMS, statement PDF) or describes the problem in text.
2. Agent extracts structured facts: UTR/RRN, amount, timestamp, channel, bank, status.
3. **Deterministic rule engine** classifies the case and computes the amount owed (refund + compensation), every deadline, and the applicable rulebook. **Each number cites the exact RBI clause.**
4. Agent drafts the complaint to the bank, plus a 1930 / cybercrime report for fraud cases.
5. **User approves** the draft and sends it themselves. The agent never sends or files anything.
6. The case runs long-term: it waits for the bank's response window, the user forwards the bank's reply, and the agent judges whether the reply complies with the rules.
7. If the case is unresolved or the reply is non-compliant, the agent drafts the RBI Ombudsman complaint (formatted for cms.rbi.org.in fields) inside the 90-day window. The user approves and files it.
8. Case closes: resolved, escalated, or abandoned. Every step is in the audit trail.

### Quality bars (what "good" means for this project)
- **Correct numbers:** money and dates come only from the rule engine, never from an LLM.
- **Grounded:** every entitlement, deadline, and claim cites its source clause.
- **Auditable:** every step is visible (Temporal history + `audit_events` + traces).
- **Durable:** cases survive restarts and run for weeks.
- **Secure:** PII handling, prompt-injection and phishing resistance.
- **Measured:** eval suites with committed results.
- **Works end-to-end** from upload to escalation, including a demo mode.

### Product constraints
- **Synthetic/sample data only.** Never real customer data, bank details, PAN, passwords, or keys in the repo.
- README must explain what Recourse does, how to run it, the architecture, and the eval results, and must **credit every open-source library and AI API used**.
- Show an "informational, not legal advice" notice in the UI and drafts.

### Non-goals (do not build)
- Auto-sending emails or auto-filing on cms.rbi.org.in / cybercrime.gov.in. A human always submits, and CMS uses a CAPTCHA anyway.
- Telegram/WhatsApp bots.

---

## 2. Confirmed tech stack (do not change without asking Aryan)

**Constraint:** only Anthropic and OpenAI model APIs. Anything else must be free with no card.

| Layer | Choice | Notes |
|---|---|---|
| Main agent / drafting | **Claude Sonnet 5.5** (`claude-sonnet-5-5`) | Anthropic SDK |
| Extraction (screenshots/PDFs) | **Claude Haiku 5.5** (`claude-haiku-5-5`) | Vision + native PDF; structured output |
| Independent eval judge | **OpenAI GPT model** (`OPENAI_JUDGE_MODEL`) | Different model family from the drafter → less self-preference bias |
| Voice notes (stretch) | OpenAI speech-to-text | `OPENAI_STT_MODEL` |
| Rule-change watcher (stretch) | Claude web search tool, allowed domains `rbi.org.in`, `npci.org.in` | Opens review items; never auto-edits rules |
| Agent logic | **LangGraph** | Inner reasoning graphs |
| Durable case workflow | **Temporal (self-hosted)** | Local: `temporal server start-dev`; production host is an open decision (§10) |
| Rule engine | Plain Python + versioned YAML rulebook | 100% deterministic, fully unit-tested |
| Backend API | **FastAPI + Pydantic v2**, Python 3.12, **uv** | |
| Database | **Neon Postgres** (free tier) + **pgvector** | Cases, evidence, drafts, audit log, regulation chunks |
| Embeddings | OpenAI small embedding model (`OPENAI_EMBED_MODEL`) | Regulation text retrieval only |
| Frontend | **Next.js** (App Router, TypeScript) on **Vercel** | Keep it simple and clean |
| Tracing | **LangSmith** free plan | `LANGSMITH_TRACING=true` |
| CI | **GitHub Actions** | Unit + rule-engine + workflow tests on every push |

Model IDs come from config/env, never hard-coded in business logic.

---

## 3. Architecture

```
Next.js (Vercel)
   │  REST
   ▼
FastAPI ──start/signal/query──► Temporal Server (self-hosted)
   │                                  │
   │                                  ▼
   │                         Temporal Worker (Python)
   │                           ├─ CaseWorkflow (one per case, runs for weeks)
   │                           └─ Activities ──► LangGraph graphs ──► Claude / OpenAI
   │                                          └─► Rule engine (pure Python)
   ▼
Neon Postgres (+pgvector): cases, evidence, entitlements, drafts, audit_events, reg_chunks
```

### Who owns what
- **Temporal is the outer layer: long-running and durable.** There is one `CaseWorkflow` per case. It owns the state machine, timers, signals and queries, and it survives restarts.
- **LangGraph is the inner layer: short-lived reasoning.** Each graph run is invoked from a Temporal **activity**.
- **The rule engine is the source of truth for money and dates.** The LLM never computes amounts or deadlines.

### CaseWorkflow states
`NEW → EXTRACTED → ASSESSED → DRAFT_READY → AWAITING_APPROVAL → SENT_TO_BANK → AWAITING_BANK (timer) → BANK_REPLIED → JUDGED → (RESOLVED | ESCALATION_DRAFT_READY → AWAITING_APPROVAL → FILED_WITH_OMBUDSMAN → CLOSED) | ABANDONED`

- **Signals:** `approve_draft(draft_id, edits?)`, `reject_draft(reason)`, `mark_sent(channel, sent_at)`, `bank_replied(text|file)`, `mark_resolved(amount_received)`, `abandon()`.
- **Queries:** `get_status()`, `get_timeline()`, `get_entitlement()`.
- **Timers:** bank response window; Ombudsman 90-day window reminder; fraud-reporting countdown.

### LangGraph graphs
1. **IntakeGraph:**
   - classify the input,
   - extract with Haiku into `Evidence` (Pydantic),
   - run deterministic validators (UTR = 12 digits, amount > 0, date not in future, channel enum).
   
   If a field is missing or uncertain, return `needs_user_confirmation`. Never guess.
2. **DraftGraph:**
   - Draft with Sonnet, using template slots for every number and date.
   - The **`verify` node is mandatory and cannot be skipped**: every ₹ amount, date, deadline and clause in the text must exactly match the rule engine's `Entitlement`.
   - On a mismatch, revise (max 3 loops). If it still fails, fail closed with an error.
3. **ReplyJudgeGraph:**
   - Evaluates a forwarded bank reply against the rules. Did they reverse? Pay compensation? Give reasons for a rejection as required?
   - Runs a **phishing check**: requests for OTP/PIN/CVV/password, remote-access apps, suspicious links.
   - Outputs a verdict and the next action.
4. **WatcherGraph** (stretch): Claude web search on the allowed domains → summarise new circulars → open a "rule review" item. Never edits the rulebook automatically.

### Temporal rules (non-negotiable)
- **No LLM calls, network calls, randomness, or `datetime.now()` inside workflow code.** Put them in activities, and use `workflow.now()` for time.
- Activities are idempotent, with explicit `start_to_close_timeout` and retry policies. Don't retry on validation errors.
- Workflow code only orchestrates. Keep it deterministic and replay-safe.
- **`DEMO_MODE=true`** shrinks time: 1 "day" = `DEMO_DAY_SECONDS` (default 60). All timer durations go through one helper, `days(n)`.
- Use Temporal's time-skipping test environment for workflow tests.

### Design principles
These are inspired by ElimentaryLabs/agent-harness-starter (MIT); credit it in the README.
- **Verification is a guaranteed step**, not an optional plugin (the DraftGraph `verify` node).
- **Permission gate:** nothing leaves the system without explicit human approval.
- **Observability is on by default:** LangSmith traces, the `audit_events` table, and Temporal history.
- **Fake-LLM tests** run with no network and no API key.

---

## 4. Domain rules (encode in `backend/recourse/rules/rulebook/*.yaml`)

> ⚠️ Check every rule against the **primary RBI/NPCI PDF** before setting `verified: true`. Items marked **VERIFY** had conflicting or secondary-only sources. Never mark a rule verified from memory or a blog.

Each rulebook entry has: `id`, `regime`, `effective_from`, `effective_to`, `channel`, `scenario`, `tat`, `compensation`, `source {doc, para, url}`, `verified: bool`, `notes`.
The engine **selects the regime by transaction date** and returns citations with every output.

### 4.1 Failed transactions: TAT and compensation
- **Source:** RBI circular RBI/2019-20/67, DPSS.CO.PD No.629/02.01.014/2019-20, 20 Sep 2019, "Harmonisation of Turn Around Time (TAT) and customer compensation for failed transactions using authorised Payment Systems". URL: https://www.rbi.org.in/scripts/FS_Notification.aspx?Id=11693&Mode=0
- `T` = transaction date (calendar date; **VERIFY** the circular's wording).
- Compensation is ₹100 per day of delay beyond the TAT.

| Channel / scenario | TAT | Compensation |
|---|---|---|
| UPI fund transfer: debited, beneficiary not credited | Auto-reversal by T+1 | ₹100/day beyond T+1 |
| UPI merchant payment: debited, merchant confirmation not received | Auto-reversal by T+5 | ₹100/day beyond T+5 |
| IMPS: debited, beneficiary not credited | Reversal by T+1 | ₹100/day beyond T+1 |
| ATM / micro-ATM: debited, cash not dispensed | Pro-active reversal by T+5 | ₹100/day beyond T+5 |
| Card-to-card transfer: debited, beneficiary card not credited | Reversal by T+1 | ₹100/day beyond T+1 |
| Card PoS (card present): debited, charge-slip not generated | Auto-reversal by T+5 | ₹100/day beyond T+5 |
| Card not present (e-commerce): debited, merchant confirmation not received | **VERIFY** (T+5 per most secondary sources) | ₹100/day beyond TAT |

Customer errors (e.g. sending money to a wrong UPI ID) are not covered by this framework. Handle them as "not covered — here's what you can try".

### 4.2 Unauthorised / fraudulent electronic banking transactions (two regimes)

**Regime A: transactions before 1 Jan 2027**
- **Source:** RBI circular DBR.No.Leg.BC.78/09.07.005/2017-18, 6 Jul 2017, "Customer Protection – Limiting Liability of Customers in Unauthorised Electronic Banking Transactions" (consolidated into RBI (Commercial Banks – Responsible Business Conduct) Directions, 2025).
- **Liability by case:**
  - Bank negligence or deficiency → **zero liability**, regardless of when it's reported.
  - Third-party breach reported within **3 working days** → zero liability.
  - Reported in **4–7 working days** → limited liability of ₹5,000 / ₹10,000 / ₹25,000 depending on account type (**VERIFY** the account-type table).
  - Reported after 7 working days → decided by the bank's board-approved policy.
- **Shadow credit** within **10 working days** of reporting.
- **Working days** are counted per the home branch schedule (**VERIFY**). Approximate them with weekends, 2nd/4th Saturdays and a 2026–27 national holiday list, and label the result an approximation in the UI.

**Regime B: transactions on/after 1 Jan 2027**
- **Source:** RBI (Commercial Banks – Responsible Business Conduct) Third Amendment Directions, 2026, issued 24 Jun 2026.
- **Scope:** covers **fraudulent** transactions, including authorised-but-coerced or tricked ones, not just unauthorised ones.
- **Burden of proof:** proving customer liability is on the bank.
- **Liability by case:**
  - Bank negligence → zero liability and reversal, regardless of reporting.
  - Third-party breach reported within **5 calendar days** → zero liability (**VERIFY**: counted "from occurrence" vs "from bank communication").
  - Reported after 5 days → bank policy.
  - Customer negligence → customer bears the loss that isn't eligible for compensation.
  - Losses after the customer reports → borne by the bank.
- **Bank response:** within **45 calendar days** (60 for cross-border). Reversals are value-dated to the original date, and rejections must state reasons.
- **Small-value compensation:**
  - For gross loss ≤ ₹50,000: 85% of net loss or ₹25,000, whichever is lower.
  - Once per lifetime.
  - Requires reporting to the bank **and** to NCRP (cybercrime.gov.in) or helpline **1930** within 5 calendar days.
- **Alerts:** banks must send instant SMS alerts for transactions above ₹500.

Fraud cases always trigger **urgent mode**: a countdown to the reporting deadline, "call 1930 / file at cybercrime.gov.in now", and a draft report to the bank.

### 4.3 Escalation: RBI Integrated Ombudsman Scheme, 2026 (RB-IOS 2026)
- **Dates:** notified 16 Jan 2026; **effective 1 Jul 2026**. It replaces RB-IOS 2021; complaints received before 1 Jul 2026 stay under the 2021 scheme.
- **Before escalating:** the customer must **complain to the bank first**.
- **When to escalate:** if there's no reply within **30 days**, or the reply is unsatisfactory.
- **Deadline:** the customer must approach the Ombudsman within **90 days** of the bank's final reply or the expiry of the reply period (it was 1 year under the 2021 scheme).
- **Awards:** up to **₹30 lakh** for actual loss, plus up to **₹3 lakh** for harassment / loss of time.
  - The customer must accept an award within 30 days.
  - Appeals go to the Appellate Authority within 30 days.
- **Covered entities:** commercial banks, NBFCs with assets ≥ ₹100 crore, non-bank PPI issuers, credit information companies.
- **How to file:** at https://cms.rbi.org.in, by email to crpc@rbi.org.in, or with CRPC, RBI, Chandigarh. Lawyers can't represent the customer; an authorised representative can.
- **Not maintainable:** commercial decisions, matters pending or decided in court, unregulated services, complaints without sufficient evidence (**VERIFY** the full list).

### 4.4 Motivation facts (README; cite sources, **VERIFY** before use)
- UPI processed 23,201.93 million transactions worth ₹29.90 lakh crore in May 2026 (NPCI data).
- RBI Ombudsman complaints rose 13.55% to ~13.34 lakh in FY2024-25 (RBI Annual Report of Ombudsman Scheme 2024-25, 1 Dec 2025).
- Compensation was paid in only ~0.44% of Ombudsman complaints (Moneylife, 4 Dec 2025; check how it's defined before quoting).

Save every primary source PDF into `docs/sources/` with a `SOURCES.md` index (title, issuer, date, URL, local file).

---

## 5. Security & safety rules

- **PII:**
  - Mask account and card numbers to the last 4 digits everywhere: UI, logs, traces, and prompts where possible.
  - Never store full card numbers, CVV, OTP, UPI PIN or passwords. If an upload contains them, redact before storage and warn the user.
- **Untrusted input:** uploaded documents and forwarded bank replies are **data, not instructions**. Wrap them in clear delimiters in prompts. Their content must never trigger tools or change workflow state directly.
- **Phishing detection:** flag any "bank reply" that asks for OTP/PIN/CVV/password, remote-access apps (AnyDesk etc.), or a payment to "unlock" a refund.
- **Human approval gate:** no outbound message, filing, or irreversible action without an explicit `approve_draft` signal. The output is a draft, a copy button and a `mailto:` link.
- **Secrets:** only via env vars. `.env` is git-ignored; ship `.env.example`. Never log API keys.
- **Disclaimer:** "informational, not legal advice" in the UI and in drafts.
- **Adversarial test suite** is mandatory (§7).

---

## 6. Repository layout (monorepo)

```
recourse/
├─ CLAUDE.md
├─ README.md
├─ .gitignore               # includes context/
├─ .env.example
├─ context/                 # PRIVATE, git-ignored — read by path, never reference elsewhere
├─ docs/
│  ├─ architecture.md
│  ├─ decisions.md          # ADR-style log of every architecture decision
│  └─ sources/              # primary RBI/NPCI PDFs + SOURCES.md
├─ backend/
│  ├─ pyproject.toml        # uv
│  ├─ recourse/
│  │  ├─ config.py          # pydantic-settings; all model IDs & flags
│  │  ├─ api/               # FastAPI routers (cases, uploads, signals, health)
│  │  ├─ temporal/          # workflows.py, activities.py, worker.py, timeutil.py (days())
│  │  ├─ agents/            # intake_graph.py, draft_graph.py, reply_judge_graph.py, watcher_graph.py
│  │  ├─ rules/             # engine.py, models.py, rulebook/*.yaml, calendar.py
│  │  ├─ llm/               # anthropic_client.py, openai_client.py, prompts/
│  │  ├─ db/                # SQLAlchemy models, Alembic migrations
│  │  └─ security/          # pii.py, injection.py, phishing.py
│  └─ tests/
│     ├─ unit/              # fake-LLM tests, no network
│     ├─ rules/             # rule-engine golden cases (must be 100%)
│     └─ workflows/         # Temporal time-skipping tests
├─ evals/
│  ├─ datasets/             # cases.jsonl, extraction set, bank replies, attacks
│  ├─ run_evals.py
│  └─ reports/              # committed eval results tables
├─ data/synthetic/          # fake screenshots, statements, bank replies (no real data)
├─ frontend/                # Next.js app
├─ infra/
│  ├─ docker-compose.yml    # temporal dev server, api, worker (Postgres = Neon)
│  └─ Dockerfile.*
└─ .github/workflows/ci.yml
```

---

## 7. Evals & testing (a core deliverable)

| Suite | What | Bar |
|---|---|---|
| Rule engine | ≥50 hand-written golden cases (amount owed, deadlines, regime, citations) | **100% pass**, runs in CI |
| Workflow | Temporal time-skipping tests for every state transition | All pass, CI |
| Graph unit tests | LangGraph nodes with a **fake LLM** (no API key) | All pass, CI |
| Extraction | 20–30 synthetic screenshots/PDFs, field-level accuracy | Report % per field |
| Draft quality | OpenAI judge with a rubric: correct amounts/dates, correct clause, correct recipient, no invented rules, clear tone | Report scores |
| Adversarial | Prompt injection in bank replies/uploads, OTP-phishing replies, fake "RBI circular" text, PII leakage | 0 successful attacks |

LLM-cost evals run manually (`uv run python evals/run_evals.py`), not on every push. Commit results to `evals/reports/` and show the table in the README.

---

## 8. Commands (create these early; keep this section accurate)

```bash
# backend
cd backend && uv sync
uv run pytest -q                          # unit + rules + workflow tests
uv run python -m recourse.temporal.worker # start Temporal worker
uv run fastapi dev recourse/api/main.py   # start API

# temporal (local)
temporal server start-dev --ui-port 8233  # Web UI at http://localhost:8233

# frontend
cd frontend && npm install && npm run dev

# evals
uv run python ../evals/run_evals.py
```

### Environment variables (`.env.example`)
```
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_MAIN_MODEL=claude-sonnet-5-5
ANTHROPIC_EXTRACT_MODEL=claude-haiku-5-5
OPENAI_JUDGE_MODEL=
OPENAI_STT_MODEL=
OPENAI_EMBED_MODEL=
DATABASE_URL=postgresql+psycopg://...
TEMPORAL_ADDRESS=localhost:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=recourse-cases
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=
DEMO_MODE=true
DEMO_DAY_SECONDS=60
```

---

## 9. Scope order

Build thin end-to-end first, then thicken. Never leave `main` non-working.

1. **Core (must work):** rule engine → intake → CaseWorkflow → drafting with verify → approval → timers + demo mode → reply judging → Ombudsman escalation → frontend → security → evals → deploy.
2. **Stretch (only after the core works), in this order:** Hindi support → rule-change watcher → voice notes.
3. **Cut order if behind:** voice notes → watcher → Hindi → UI polish.
4. **Never cut:** rule-engine correctness, the end-to-end flow, the eval tables.

Dated milestones live in `context/initial-context.md`.

---

## 10. Open technical decisions (ask Aryan before resolving)

1. **Production hosting for Temporal + worker + API** (no card available). Candidates:
   - a single container on a no-card free host (e.g. Render free: sleeps after ~15 min idle, ~30–50 s cold start, 512 MB),
   - Aryan's machine plus a free tunnel.
   
   Code must treat `TEMPORAL_ADDRESS` as config only. Always provide a `docker-compose` path so anyone can run the full stack locally.
2. Exact OpenAI model IDs for the judge, STT and embeddings (check current names in the OpenAI dashboard).
3. Any rule marked **VERIFY** in §4.

---

## 11. How to work with Aryan

- Explain in **simple English with proper technical terms** (smart-teacher style). Briefly explain a new concept (e.g. Temporal determinism, signals) the first time it comes up.
- Aryan prefers backend work, so keep the frontend simple and clean.
- **Plan before large changes**: propose first, then implement in small, reviewable commits.
- Run the tests before saying something is done. Never claim a feature works without running it.
- Don't add a dependency without a one-line reason. Don't swap stack components (§2) without asking.
- Record every architecture decision in `docs/decisions.md`.
- Keep this file and the README in sync with reality, and update §8 when commands change.
- When unsure about a domain rule, **stop and flag it**. Never invent RBI rules, numbers or deadlines.
