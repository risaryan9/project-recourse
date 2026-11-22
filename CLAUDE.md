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

   **Exceptions Aryan approved (22 Nov 2026), and nothing beyond them:** the event's name and
   website may appear in (a) the README's single "Built for …" line under the title, and (b) the
   close slide (`frontend/components/slides/close-slide.tsx`, `docs/slides/03-close.png`). Never
   in code comments, docs, commit messages or anywhere else.
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
5. **User approves** the draft. Only then is it sent: through the user's own Gmail if they connected it (`GmailChannel`, the default), otherwise the user sends it themselves (`ManualChannel`). Nothing is sent without explicit approval of that specific email, and nothing is ever filed by the agent.
6. The case runs long-term: it waits for the bank's response window while the reply watcher checks the email thread (or the user pastes/forwards the reply), and the agent judges whether the reply complies with the rules.
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
- Sending anything without explicit per-email approval.
- Auto-filing on cms.rbi.org.in / cybercrime.gov.in. The human always files, and CMS uses a CAPTCHA anyway.
- Telegram/WhatsApp bots.

---

## 2. Confirmed tech stack (do not change without asking Aryan)

**Constraint:** only Anthropic and OpenAI model APIs. Anything else must be free with no card.

| Layer | Choice | Notes |
|---|---|---|
| Main agent / drafting | **Claude Sonnet 5.5** (`claude-sonnet-5-5`) | Anthropic SDK. Also drafts in Hindi (stretch) |
| Extraction (screenshots/PDFs) | **Claude Haiku 5.5** (`claude-haiku-5-5`) | Vision + native PDF; structured output |
| Reply watcher classification | **Claude Haiku 5.5** | Labels each new bank message (acknowledgement / substantive) |
| LLM fallback provider | **OpenAI** `gpt-6.1-sol` (MAIN) / `gpt-6.1-terra` (EXTRACT) | `FallbackLLM`: used only when Anthropic is unavailable or rejects the call (ADR-005) |
| Independent eval judge | **OpenAI GPT model** (`OPENAI_JUDGE_MODEL`) | Different model family from the drafter → less self-preference bias |
| Email channel | **Gmail API** (`GmailChannel`, default) + `ManualChannel` fallback | OAuth, minimal scopes; see §3 "Email channel" |
| Rule-change watcher (stretch) | Claude web search tool, allowed domains `rbi.org.in`, `npci.org.in` | Opens review items; never auto-edits rules |
| Agent logic | **LangGraph** | Inner reasoning graphs |
| Durable case workflow | **Temporal (self-hosted)** | Local: `temporal server start-dev`; production host is an open decision (§12) |
| Rule engine | Plain Python + versioned YAML rulebook | 100% deterministic, fully unit-tested |
| Backend API | **FastAPI + Pydantic v2**, Python 3.12, **uv** | |
| Database | **Neon Postgres** (free tier) + **pgvector** | Cases, evidence, drafts, audit log, regulation chunks. Used from day one, in development too (no local Postgres); branches `main` / `dev` / `test` (ADR-007) |
| Embeddings | OpenAI small embedding model (`OPENAI_EMBED_MODEL`) | Regulation text retrieval only |
| Frontend | **Next.js** (App Router, TypeScript) + **Tailwind** + **shadcn/ui** on **Vercel** | Theme in `DESIGN.md`; see §6 |
| Tracing | **LangSmith** free plan | `LANGSMITH_TRACING=true` |
| CI | **GitHub Actions** | Unit + rule-engine + workflow tests on every push |

Model IDs come from config/env, never hard-coded in business logic. Models are Anthropic and OpenAI only.

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
   │                                          ├─► Rule engine (pure Python)
   │                                          └─► EmailChannel ──► Gmail API (send, thread read)
   ▼
Neon Postgres (+pgvector): cases, evidence, entitlements, drafts, audit_events, reg_chunks
```

### Who owns what
- **Temporal is the outer layer: long-running and durable.** There is one `CaseWorkflow` per case. It owns the state machine, timers, signals and queries, and it survives restarts.
- **LangGraph is the inner layer: short-lived reasoning.** Each graph run is invoked from a Temporal **activity**.
- **The rule engine is the source of truth for money and dates.** The LLM never computes amounts or deadlines.

### CaseWorkflow states
`NEW → EXTRACTED → ASSESSED → DRAFT_READY → AWAITING_APPROVAL → SENT_TO_BANK → AWAITING_BANK (timer) → BANK_REPLIED → JUDGED → (RESOLVED | ESCALATION_DRAFT_READY → AWAITING_APPROVAL → FILED_WITH_OMBUDSMAN → CLOSED) | ABANDONED`

- **Signals:** `approve_draft(draft_id)`, `reject_draft(draft_id)` (edits, name and reason are stored on the draft row by the API, ADR-011), `redraft()` (after a draft failed closed), `mark_sent(channel, sent_at)` (ManualChannel only; GmailChannel records the send itself), `retry_send()` (after a blocked send was fixed, ADR-012), `bank_replied(reply_id)` (the API stores the pasted/forwarded reply first, ADR-014), `mark_resolved(amount_received)`, `escalate()` (the user finds a reply unsatisfactory), `recheck_escalation()` (after the user gave the bank's type), `mark_filed(filed_at, reference)` (the user filed on cms.rbi.org.in), `close(outcome)`, `abandon()`.
- **Queries:** `get_status()`, `get_timeline()`, `get_entitlement()`.
- **Timers:** bank response window; Ombudsman 90-day window reminder; fraud-reporting countdown; Gmail reply-watcher poll interval.

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
   - Evaluates a substantive bank reply (from the reply watcher, or pasted/forwarded by the user) against the rules. Did they reverse? Pay compensation? Give reasons for a rejection as required?
   - Runs a **phishing check** (requests for OTP/PIN/CVV/password, remote-access apps, suspicious links) and a **sender-domain check** against the bank contacts file.
   - **Has no tools.** Its LLM output is a fixed enum of labels plus extracted fields; the workflow decides the next action from that verdict, and any consequential action still needs approval.
4. **WatcherGraph** (stretch): Claude web search on the allowed domains → summarise new circulars → open a "rule review" item. Never edits the rulebook automatically.

### Email channel (Gmail by default)

`EmailChannel` is one interface with two implementations:
- **`GmailChannel`**: the default when the user has connected Gmail.
- **`ManualChannel`**: the fallback. Copy button + `mailto:` link to send; the user pastes or forwards the bank's reply. **ManualChannel must always work**, with or without Gmail.

**OAuth**
- Scopes: `openid`, `email`, `gmail.send`, `gmail.readonly`. **Nothing else.**
- `access_type=offline`, `prompt=consent`.
- Refresh tokens are encrypted at rest with `TOKEN_ENCRYPTION_KEY`. A **"Disconnect"** action deletes the tokens.

**Sending**
- Only after an explicit `approve_draft` signal for **that specific email**.
- The subject carries a unique case tag, e.g. `[RC-7F3K]`. Store the Gmail `messageId` and `threadId` on the case.
- Once the bank's complaint/ticket number is known, include it in follow-up subjects.

**Sandbox (on by default)**
- With `EMAIL_SANDBOX=true`, every outgoing email goes to `SANDBOX_RECIPIENT`, and the UI shows a **"Sandbox — not sent to the real bank"** badge.
- Real sending requires `EMAIL_SANDBOX=false` **and** a recipient from a `verified` bank contacts entry **and** verified cited rules (ADR-012).

**Reply watcher**
- A Temporal loop calls `threads.get(threadId)` every `GMAIL_POLL_MINUTES` (default 30; in `DEMO_MODE`, every `DEMO_GMAIL_POLL_SECONDS`). The Gmail calls run in activities; the loop and its timer live in the workflow.
- It runs until the case closes. During the bank's window a reply moves the case on; after the window (Ombudsman steps), late replies are judged and shown without changing the Ombudsman steps, checked at least 6 hours apart outside demo mode (ADR-021).
- Each message is processed once: store processed message IDs. Ignore messages sent by the user.
- Classify each new bank message (Haiku):
  - **acknowledgement** → auto-extract the complaint number, keep the timer running;
  - **substantive reply** → `ReplyJudgeGraph`.
- **Unthreaded replies:** exactly one fallback query is allowed: `from:(<known bank domains>) after:<sent date>`, plus the case tag or complaint number. The user must confirm **"Is this about your case?"** before any LLM sees the message.

**Hard rule: thread-only access.** `GmailChannel` exposes only `send()`, `get_thread(thread_id)` and `bank_scoped_search()`. A unit test must fail if any other Gmail endpoint (e.g. an unscoped list or search) is called. Every Gmail API call is logged to `audit_events` and shown on the case timeline.

**Bank contacts file** (`backend/recourse/email/bank_contacts.yaml`): ~10 major Indian banks. Per bank: grievance email, nodal officer contact, known sender domains, source URL, `verified` flag. Data only from official bank websites; mark entries **VERIFY** until checked.

**Google OAuth app (deployed instance)**
- External, published but **unverified**: users see Google's warning, and there is a 100-user lifetime cap.
- Google verification + CASA assessment is a post-launch step; the README must say so.
- Gotchas to document in the README:
  - use personal Gmail accounts (Workspace admins may block the app),
  - redirect URIs must match exactly,
  - the test-user count is permanent,
  - tokens issued in Testing mode expire after 7 days.

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

**The YAML rulebook is the single source of truth**; this section is a summary of it. Every value in the YAML carries exact `quotes` from its source, and `tests/rules/test_rulebook.py` fails if a quote is not found in the local PDF. Design and conventions: `docs/decisions.md` ADR-002.
The engine (`rules/engine.py`) **selects the regime by transaction date (and bank type for fraud)**, takes an explicit `as_of` date, and returns citations with every output. All rules start `verified: false`; Aryan flips them after checking the PDF.

### 4.1 Failed transactions: TAT and compensation (`failed_transactions.yaml`)
- **Source:** RBI/2019-20/67 (20 Sep 2019), effective **15 Oct 2019**, Annex. Domestic transactions only.
- `T` = day of transaction, a **calendar date** (Annex item 4). Compensation is ₹100 per calendar day of delay beyond T+TAT, credited suo moto (para 5).

| Channel / scenario (Annex row) | TAT |
|---|---|
| ATM / micro-ATM: cash not dispensed (1a) | T+5 |
| Card-to-card: beneficiary card not credited (2a) | T+1 |
| Card PoS (card present): charge-slip not generated (2b) | T+5 |
| Card not present (e-commerce): no merchant confirmation (2c, merged cell with 2b) | T+5 |
| IMPS: beneficiary not credited (3a) | T+1 |
| UPI transfer: beneficiary not credited (4a) | T+1 |
| UPI merchant payment: no merchant confirmation (4b) | T+5 |
| AePS: merchant not confirmed / beneficiary not credited (5a, 5b merged) | T+5 |
| APBS: beneficiary not credited (6a) | T+1 |
| NACH: not credited (7a); debit despite revoked mandate (7b, shares 7a's compensation cell) | T+1 |
| PPI on-us: beneficiary not credited / merchant not confirmed (8b) | T+1 |

- PPI off-us transactions follow the underlying rail (8a).
- **Open question (flagged `PAYEE_AMBIGUOUS`):** principle 1(a) says the penalty for an uncredited credit-push transfer "has to be paid to the beneficiary"; we show it as owed to the customer.
- Customer errors (e.g. a wrong UPI ID) are not covered. Handle them as "not covered — here's what you can try".

### 4.2 Unauthorised / fraudulent electronic banking transactions (`fraud_liability.yaml`)

| Regime | Transactions | Source | Bank types covered |
|---|---|---|---|
| `FRAUD-A-2017` | 6 Jul 2017 – 27 Nov 2025 | 2017 circular DBR.No.Leg.BC.78 (now withdrawn) | Commercial, RRB, SFB, Payments Bank |
| `FRAUD-A-2025` | 28 Nov 2025 – 31 Dec 2026 | RBC Directions 2025, paras 64–76 (same rules) | Commercial banks only |
| `FRAUD-B-2027` | from 1 Jan 2027 | RBC Third Amendment Directions 2026, paras 76A–76V | Commercial banks only |

Other bank types → **fail closed** (`BANK_TYPE_NOT_IN_RULEBOOK`): no liability figures, but still the 1930 guidance.

**Regime A**
- Bank negligence → zero liability, regardless of reporting.
- Third-party breach, counted in **working days from receipt of the bank's alert**, excluding that day (home-branch schedule; approximated, see ADR-002): ≤3 → zero; 4–7 → min(txn value, Table 1 cap: ₹5,000 BSBD / ₹10,000 savings, PPI, MSME, individual current ≤₹25 lakh, credit card ≤₹5 lakh / ₹25,000 other current, credit card >₹5 lakh); >7 → bank policy.
- Customer negligence → customer bears the loss until reporting; the bank bears losses after.
- Shadow reversal within **10 working days** of notification; bank must resolve within **90 days**. Burden of proof on the bank.

**Regime B**
- Covers fraudulent EBTs, including coerced approvals (para 4(15A)). Burden of proof on the bank (76K).
- Bank negligence → zero liability and reversal (76L).
- Third-party breach reported within **5 calendar days from the transaction** → zero; later → bank policy (76M). No limited-liability tier.
- Customer negligence → customer bears the loss not covered by compensation, until reporting (76N); losses after reporting → bank (76O).
- Bank must resolve within **45 calendar days** (60 cross-border) of the complaint (76Q). Reversals are value-dated; rejections need reasons (76R, 76S).
- Shadow reversal within 5 calendar days **for credit cards only** (76R).
- **Small-value compensation (76T/76U), customer-negligence cases only:** individual (incl. sole proprietor), gross loss ≤ ₹50,000, min(85% of net loss, ₹25,000), once per lifetime, reported to the bank **and** NCRP/1930 within 5 calendar days, for frauds occurring up to 31 Dec 2027. Subject to the bank's bona fide check.
- Banks must send instant SMS alerts for EBTs above ₹500 (76D).

Fraud cases always trigger **urgent mode**: a countdown to the reporting deadline, "call 1930 / file at cybercrime.gov.in now", and a draft report to the bank.

### 4.3 Escalation: RB-IOS 2026 (`ombudsman.yaml`)
- **Dates:** notified 16 Jan 2026; **effective 1 Jul 2026**. Complaints received before then stay under RB-IOS 2021 (not modelled).
- **Complain to the bank first** (10(1)(e)).
- **Waiting period:** no reply within the **higher** of 30 days and the RBI/NPCI/card-network timeline (90 days for Regime A fraud, 45/60 for Regime B), or an unsatisfactory reply (10(1)(f)).
- **Deadline:** within **90 days** of the later of the waiting period's end and the bank's last communication (10(1)(g)).
- **Awards:** no cap on the disputed amount; up to **₹30 lakh** for consequential loss and **₹3 lakh** for time/harassment (8(3)). Accept within 30 days (15(4)); appeal within 30 days (17(3)).
- **Covered:** all commercial banks (incl. SFB, Payments Bank, LAB), RRBs, non-bank PPI issuers, credit information companies. Co-operative banks and NBFCs only above size thresholds → `CONDITIONAL`.
- **How to file:** https://cms.rbi.org.in, crpc@rbi.org.in, or CRPC, RBI, Chandigarh. No advocates (unless they are the aggrieved person); an authorised representative is allowed.
- **Not maintainable / excluded:** see clause 10(1)–(2) of the scheme (same grievance pending in court or already decided, commercial judgment, vendor disputes, Limitation Act expiry, etc.).

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
- **Human approval gate:** no outbound message, filing, or irreversible action without an explicit `approve_draft` signal for that specific item. GmailChannel sends only after it; ManualChannel shows a draft, a copy button and a `mailto:` link.
- **Gmail access:** thread-only, minimal scopes, every call audited (§3 "Email channel").
- **Secrets:** only via env vars. `.env` is git-ignored; ship `.env.example`. Never log API keys or OAuth tokens.
- **Disclaimer:** "informational, not legal advice" in the UI and in drafts.
- **Adversarial test suite** is mandatory (§8).

### Threat model

| Threat | Mitigation |
|---|---|
| Prompt injection in emails | Email content is untrusted data; LLM output is a fixed enum of labels; `ReplyJudgeGraph` has no tools; consequential actions need approval |
| Phishing | Phishing check + sender-domain check against the bank contacts file |
| Over-reading the mailbox | Thread-only code path; unit test that fails on any list/unscoped call; Gmail call audit log |
| Token theft | Encrypted refresh tokens; minimal scopes; Disconnect deletes tokens |
| Data sent to the LLM provider | Redact before the LLM; store only what's needed |
| Wrong recipient | Sandbox by default; bank contacts allowlist; per-email approval |

**Honest caveat:** `gmail.readonly` technically allows reading the whole mailbox. The thread-only restriction is enforced in our code, not by Google, so it must be provable: the no-list unit test and the audit log are that proof.

The README must have a **"Security & Privacy"** section with this table and the attack-suite results.

---

## 6. Frontend

**Stack (locked in):** Next.js + Tailwind + shadcn/ui.
- `DESIGN.md` at the repo root holds the design language (near-black, one electric-blue accent, glass, Mona Sans), set by the landing page. Follow it for every screen.
- Use Anthropic's **frontend-design** skill when building screens.
- Run **Vercel's Web Interface Guidelines** review before any demo.

**Screens**
- **Cases (home):** case cards with amount owed + next-deadline countdown; Gmail status chip; Sandbox badge; "+ New case".
- **New case:** upload / paste / describe → editable extracted fields (low-confidence fields highlighted) → Assess.
- **Case page:** header; "What you're owed" with RBI-clause links; one "Next action" panel; tabs **Timeline / Emails / Evidence**. Must be **mobile-friendly**.
- **Settings:** Gmail connect/disconnect, sandbox toggle, English/Hindi, delete my data.
- **`/insights`** (hidden): eval table, attack-suite score, watcher review queue.

---

## 7. Repository layout (monorepo)

```
recourse/
├─ CLAUDE.md
├─ DESIGN.md                # frontend theme (shadcn)
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
│  │  ├─ temporal/          # case_workflow.py, case_activities.py, case_types.py, case_runner.py, worker.py, timeutil.py (days())
│  │  ├─ agents/            # intake_graph.py, intake_schema.py, draft_graph.py, reply_judge_graph.py, watcher_graph.py
│  │  ├─ cases/             # assessment.py (Evidence → rule engine), review.py (user answers)
│  │  ├─ evidence/          # models.py (Evidence, Extracted[T]), validators.py (check_evidence)
│  │  ├─ rules/             # engine.py, models.py, rulebook/*.yaml, calendar.py
│  │  ├─ email/             # channel.py (EmailChannel), gmail.py, manual.py, oauth.py, bank_contacts.yaml
│  │  ├─ llm/               # base.py, prompting.py, anthropic_client.py, openai_client.py, fallback.py, fake.py
│  │  ├─ db/                # models.py (SQLAlchemy), session.py, audit.py, case_tag.py
│  │  └─ security/          # pii.py, injection.py, phishing.py
│  ├─ migrations/           # Alembic (env.py, versions/)
│  └─ tests/
│     ├─ unit/              # fake-LLM tests, no network
│     ├─ rules/             # rule-engine golden cases (must be 100%)
│     ├─ db/                # Postgres tests (Neon test branch, opt-in: -m db)
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

## 8. Evals & testing (a core deliverable)

| Suite | What | Bar |
|---|---|---|
| Rule engine | ≥50 hand-written golden cases (amount owed, deadlines, regime, citations) | **100% pass**, runs in CI |
| Workflow | Temporal time-skipping tests for every state transition | All pass, CI |
| Graph unit tests | LangGraph nodes with a **fake LLM** (no API key) | All pass, CI |
| Extraction | 20–30 synthetic screenshots/PDFs, field-level accuracy | Report % per field |
| Draft quality | OpenAI judge with a rubric: correct amounts/dates, correct clause, correct recipient, no invented rules, clear tone | Report scores |
| Gmail access | Unit test that fails if `GmailChannel` calls any endpoint other than send / thread get / bank-scoped search | Pass, CI |
| Adversarial | Prompt injection in bank replies/uploads, OTP-phishing replies, fake "RBI circular" text, PII leakage. **Email attacks:** injection inside a bank reply, OTP-phishing reply, spoofed sender domain, unthreaded reply | **0 successful attacks** |

LLM-cost evals run manually (`uv run python evals/run_evals.py`), not on every push. Commit results to `evals/reports/` and show the table in the README.

---

## 9. Commands (create these early; keep this section accurate)

```bash
# backend
cd backend && uv sync
uv run pytest -q                          # unit + rules + workflow tests
uv run pytest -q -m db                    # database tests on the Neon test branch (TEST_DATABASE_URL)
uv run alembic upgrade head               # apply migrations to DATABASE_URL (Neon dev branch)
uv run python -m recourse.temporal.worker # start Temporal worker
uv run fastapi dev recourse/api/main.py   # start API
neon checkout dev                         # Neon CLI: pin the dev branch (see ADR-007)
uv run python scripts/try_intake.py "<text>" [--file img.png]  # one real intake run (synthetic data only)
uv run python scripts/try_draft.py [--fraud]                    # one real draft on a built-in synthetic case

# temporal (local)
temporal server start-dev --ui-port 8233  # Web UI at http://localhost:8233

# frontend
cd frontend && npm install && npm run dev

# evals (from backend/)
uv run python ../evals/run_evals.py                  # all suites (paid: extraction, drafts, attacks)
uv run python ../evals/run_evals.py --suites rules   # free suite only
uv run python ../evals/make_synthetic.py             # rebuild the synthetic dataset and screenshots
```

### Environment variables (`.env.example`)
```
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_MAIN_MODEL=claude-sonnet-5-5
ANTHROPIC_EXTRACT_MODEL=claude-haiku-5-5
ANTHROPIC_MAX_RETRIES=2
ANTHROPIC_TIMEOUT_SECONDS=120
OPENAI_MAIN_MODEL=gpt-6.1-sol
OPENAI_EXTRACT_MODEL=gpt-6.1-terra
LLM_PROVIDERS=["anthropic","openai"]
OPENAI_JUDGE_MODEL=gpt-6.1-sol
OPENAI_EMBED_MODEL=
DATABASE_URL=postgresql+psycopg://...neon.tech/neondb?sslmode=require
TEST_DATABASE_URL=
TEMPORAL_ADDRESS=localhost:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=recourse-cases
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
TOKEN_ENCRYPTION_KEY=
EMAIL_SANDBOX=true
SANDBOX_RECIPIENT=
GMAIL_POLL_MINUTES=30
DEMO_MODE=true
DEMO_DAY_SECONDS=60
DEMO_GMAIL_POLL_SECONDS=10
FRONTEND_URL=http://localhost:3000   # where the API sends the browser back after Google sign-in
# Vercel (frontend) env, not this file: NEXT_PUBLIC_API_URL=/api, BACKEND_URL=<API's public URL> (ADR-022)
CORS_ORIGINS=["http://localhost:3000"]
OWNER_COOKIE_SECURE=false        # true (HTTPS) when the frontend and API are on different sites
OWNER_COOKIE_SAMESITE=lax        # none for a cross-site deployment
```

---

## 10. Scope order

Build thin end-to-end first, then thicken. Never leave `main` non-working.

1. **Core (must work):** rule engine → intake → CaseWorkflow → drafting with verify → approval → timers + demo mode → **EmailChannel + ManualChannel** → reply judging → Ombudsman escalation → frontend → **GmailChannel + reply watcher** → security → evals → deploy.
2. **Stretch (only after the core works), in this order:** Hindi support → rule-change watcher.
3. **Cut order if behind:** rule-change watcher → Hindi → UI polish.
4. **Never cut:** rule-engine correctness, the end-to-end flow, ManualChannel, the eval tables.

Dated milestones live in `context/initial-context.md`.

---

## 11. Cost budget

- **≈ $0.06 per full case** (mostly Sonnet drafting); **≈ $12–20 for the whole build**.
- Unit tests and CI use the **fake LLM**: no API cost.
- Use **prompt caching** for system prompts and rulebook text.
- The reply watcher's classification runs on **Haiku**.

---

## 12. Open technical decisions (ask Aryan before resolving)

1. ~~Production hosting~~: resolved (ADR-022). Frontend on Vercel (`https://recourse-omega.vercel.app`); Temporal, worker and API on Aryan's laptop behind an ngrok tunnel; `frontend/proxy.ts` serves `/api/*` and `/auth/*` from the Vercel origin. Always-on alternative if needed: a single container on a no-card free host.
   
   Code must treat `TEMPORAL_ADDRESS` as config only. Always provide a `docker-compose` path so anyone can run the full stack locally.
   - **Escape hatch:** if hosting Temporal fails, **DBOS** (durable workflows on Postgres only) can replace it.
2. Exact OpenAI model ID for embeddings (check current names in the OpenAI dashboard). The judge is resolved: `OPENAI_JUDGE_MODEL=gpt-6.1-sol`.
3. Any rule marked **VERIFY** in §4, and any bank contact marked **VERIFY**.
4. ~~`DESIGN.md` theme~~: resolved. Custom design language in `DESIGN.md` (no shadcnblocks theme).

---

## 13. How to work with Aryan

- Explain in **simple English with proper technical terms** (smart-teacher style). Briefly explain a new concept (e.g. Temporal determinism, signals) the first time it comes up.
- Aryan prefers backend work, so keep the frontend simple and clean.
- **Plan before large changes**: propose first, then implement in small, reviewable commits.
- Run the tests before saying something is done. Never claim a feature works without running it.
- Don't add a dependency without a one-line reason. Don't swap stack components (§2) without asking.
- Record every architecture decision in `docs/decisions.md`.
- Keep this file and the README in sync with reality, and update §9 when commands change.
- When unsure about a domain rule, **stop and flag it**. Never invent RBI rules, numbers or deadlines.
