# Recourse design language

The landing page (`frontend/app/page.tsx`) set this language; the app follows it, calmer and
denser. Tokens live in `frontend/app/globals.css`; shadcn/ui components read them through the
standard shadcn variables, so new components inherit the look.

## The feeling

People arrive feeling small and ignored by their bank. Recourse should move them to quiet
confidence: someone competent is on their side. Calm, precise, premium, trustworthy, like a
well-made instrument. Never loud fintech, crypto neon, a SaaS template, a cluttered dashboard
or a government site.

## Light and colour

The product is **lit from within**: a near-black base with a few soft pools of blue light.
Hierarchy comes from size, weight and light, not from extra hues.

| Token | Value | Use |
|---|---|---|
| `--ink` | `#050507` | The base. Never flat: the ambient light and a faint grain sit on it. |
| `--text` | `#f1f3f7` | Primary text (soft white). |
| `--text-2` | `#a4aab6` | Body and supporting text. |
| `--text-3` | `#6c7280` | Quiet labels, dates, footnotes. |
| `--line` | `rgb(255 255 255 / 0.08)` | Barely-there edges and dividers. |
| `--blue` | `#3b82f6` | **The one accent.** Fills (the key action, progress, markers). |
| `--blue-bright` | `#6ea5ff` | Blue *text* on black (amounts owed, step numbers), for contrast. |

Rules for blue:
- One filled blue control per screen: the key action.
- Blue marks meaning: what you're owed, the current step, the line that proves a point.
- If more than a few things on a screen are blue, remove some.
- No second accent colour. A warning (for example a scam reply) is told in words and layout,
  not in red. `--amber` exists for the app's rare states only.

A fine 64 px line grid sits on the ink, lit by the same light: brightest near the top-right
pool and fading into the black (`.ambient-grid` on the landing page, fainter in the app). It is
static and painted with gradients, so it costs nothing on scroll.

## Glass

Glass is how product UI and important content are presented: a translucent panel that catches
a little blue light, with a thin edge.

- `.glass`: painted frost (translucent fill, a top sheen, blue light from the top right), a
  `--line` border, an inset top highlight and a long soft shadow.
- `.glass-lit`: adds a faint blue catch-light along the top edge. Use it on the panel that
  matters most in a view, not on every panel.
- **No `backdrop-filter`.** Measured: re-blurring the drifting light behind the panels halved
  the scroll frame rate (30 fps on desktop). The light behind is smooth, so the painted frost
  looks the same.
- Radius by role: large product panels `1.75rem`; small panels and notices `1.25rem`; inner
  boxes `1rem`; pills and buttons fully round.

## Type

**Mona Sans**, one family, variable weight and width (`next/font/google`, subsets `latin` and
`latin-ext`; `latin-ext` carries the **₹** glyph, which many grotesks lack).

| Class | Size | Weight / width | Use |
|---|---|---|---|
| `.type-display` | `clamp(2.6rem, …, 5.4rem)` | 620 / 90%, tracking −0.035em, leading 1 | One per page: the hero statement. |
| `.type-title` | `clamp(2rem, …, 3.4rem)` | 620 / 88% | Section titles. |
| `.type-heading` | `clamp(1.35rem, …, 1.75rem)` | 600 / 92% | Item headings, step titles. |
| `.type-lede` | `clamp(1.125rem, …, 1.3rem)` | 400, `--text-2` | The sentence under a title. |
| body | `1.0625rem / 1.6` | 400 | Everything else. |
| `.type-small` | `0.875rem` | 400, `--text-3` | Footnotes, sources, disclaimers. |

- Headlines are slightly condensed, tight and balanced (`text-wrap: balance`). Body lines stay
  under ~65 characters (`max-w-[42ch…52ch]`).
- **Money is a hero element.** `.figures` uses Mona Sans's *proportional* lining figures:
  its tabular set is drawn mono-style and reads as code. Use `.figures-tabular` only where
  digits must not move: while a number is counting, and in dense columns in the app.
- Indian formatting everywhere: `₹1,25,000.00` (`frontend/lib/format.ts`, same as the
  backend's letters). Dates as `14 Nov 2026` in UI, `14 November 2026` in letters.
- Sentence case. No all-caps labels, no eyebrow labels above headings, no "→" on buttons, no
  middle-dot meta strings, no single highlighted word in a headline.

## Space and composition

- One 72rem column (`max-w-6xl`, `px-6`), left-aligned. Sections connect into one story with
  generous vertical rhythm (`py-24` to `py-40`).
- Text left, proof right: a statement paired with real product UI in glass.
- Structure means something: numbers only for real sequences (the case's steps); dividers only
  between items of a list.
- Empty space is intentional. If a screen feels crammed, remove something.

## Motion

Slow, eased, graceful: `--ease-calm` = `cubic-bezier(0.22, 1, 0.36, 1)`, durations 0.5–1.4 s.
No bounce, no jitter, no loops (except the ambient light's very slow drift).

- **Two or three showpiece moments per page, no more.** On the landing page:
  1. the hero ledger, where what you're owed comes into being (plays once, about 2.6 s);
  2. the pinned case, whose card moves forward with your scroll;
  3. a scam reply being caught (a blue underline sweeps the dangerous sentence, then a calm
     verdict).
- Everything else is still, or a gentle reveal (`Reveal`: opacity and 14 px, once).
- Scroll is never hijacked: the pinned section is `position: sticky` and only *reads* scroll
  progress. Phones get no pinning (each step shows its own card).
- Animate only `transform` and `opacity` (and, once, a background-size underline). Counting
  numbers write to the DOM directly, not through React state.
- **Reduced motion** shows the final state of everything at once, with no drift or parallax.
- Performance bar (checked with Playwright, CPU throttled 4×): 60 fps scroll on desktop
  (1440×900) and phone (390×844), no horizontal overflow, no console errors.

## Voice

Calm and reassuring, like a good advisor. Short, clear sentences. Concrete numbers over hype.
Name things as people know them ("your bank's reply", not "inbound message"). Buttons say
exactly what happens ("Start a case", "Approve and send"). No exclamation marks, no
fear-mongering, no jargon (say "within one day", not "T+1").

Never: fake testimonials, made-up user counts, bank logos, the RBI emblem, or claims the
product doesn't fulfil. Every figure shown is either from the rule engine or a clearly
labelled example. The footer always says: "Informational, not legal advice. Not affiliated
with RBI or NPCI."

## The app

Same black, blue, glass and type, but calmer and denser, built for getting work done:
- Fewer glows (one ambient pool), no showpiece motion; motion only answers actions
  (a step completing, a panel opening).
- Body at `1rem`; tables and lists use `.figures-tabular` so columns align.
- One "Next action" panel per case, in `.glass .glass-lit`; everything else in plain `.glass`
  or on the ink.
- Status as a quiet pill with a dot (blue dot when the case is waiting on something live).
- **A case keeps one frame while it moves** (`components/case/case-shell.tsx`): the header, a
  six-stage tracker (done, current with whose turn it is, still to come) and a sidebar with the
  case's pages (Overview, Letters and replies, Timeline, Evidence) and key facts. Only the body
  of the Next action panel changes from step to step; its header always reads "Step n of 6".
  The step, stage and owner come from one function (`lib/case-progress.ts`).
- The Timeline page draws every rule window on one date axis (time gone filled in, today as a
  dashed line, milestones as dots). While Gmail watches the thread, a quiet widening ring says
  Recourse is looking for the reply; pasting a reply is the fallback for one sent elsewhere.
