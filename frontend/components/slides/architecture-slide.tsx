import { Fragment, type ReactNode } from "react";

import { LogoTile, SlideFrame } from "@/components/slides/frame";
import { cn } from "@/lib/utils";

/*
 * The system as it is in the code (checked against backend/recourse and frontend/), laid out
 * on the 1920x1080 slide in absolute pixels so the connectors meet the panels exactly.
 */

type Rect = { x: number; y: number; w: number; h: number };

function Box({ r, lit = false, className, children }: { r: Rect; lit?: boolean; className?: string; children: ReactNode }) {
  return (
    <section
      className={cn("glass absolute flex flex-col rounded-[1.5rem] p-6", lit && "glass-lit", className)}
      style={{ left: r.x, top: r.y, width: r.w, height: r.h }}
    >
      {children}
    </section>
  );
}

function Head({ logos, title, sub }: { logos: [string, string][]; title: string; sub?: string }) {
  return (
    <div className="flex items-center gap-3.5">
      {logos.map(([src, name]) => (
        <LogoTile key={name} src={src} name={name} />
      ))}
      <div className="min-w-0">
        <h3 className="text-[22px] leading-tight font-[600] tracking-[-0.01em] [font-stretch:92%]">{title}</h3>
        {sub ? <p className="text-[15px] leading-snug text-text-3">{sub}</p> : null}
      </div>
    </div>
  );
}

function Steps({ items, accent }: { items: string[]; accent?: string }) {
  return (
    <div className="flex flex-col items-start gap-1.5">
      {items.map((s, i) => (
        <Fragment key={s}>
          {i > 0 ? <span aria-hidden="true" className="ml-4 h-2.5 w-px bg-line-strong" /> : null}
          <span
            className={cn(
              "rounded-full border px-3 py-1 text-[15px] whitespace-nowrap",
              s === accent ? "border-blue/60 bg-blue/15 text-text" : "border-line-strong text-text-2",
            )}
          >
            {s}
          </span>
        </Fragment>
      ))}
    </div>
  );
}

function List({ title, items, note }: { title: string; items: string[]; note?: string }) {
  return (
    <div className="flex flex-col gap-2">
      <h4 className="text-[15px] text-text-3">{title}</h4>
      <ul className="flex flex-col gap-1.5 text-[16px] leading-snug text-text-2">
        {items.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>
      {note ? <p className="text-[14px] text-text-3">{note}</p> : null}
    </div>
  );
}

const STATES = ["Extracted", "Assessed", "Awaiting approval", "Sent to bank", "Awaiting bank", "Judged", "Resolved or Ombudsman", "Closed"];

/* Panel rectangles (px on the 1920x1080 slide). */
const FRONT: Rect = { x: 80, y: 236, w: 320, h: 178 };
const API: Rect = { x: 80, y: 476, w: 320, h: 178 };
const DB: Rect = { x: 80, y: 752, w: 320, h: 206 };
const TEMPORAL: Rect = { x: 470, y: 236, w: 990, h: 388 };
const RULES: Rect = { x: 470, y: 684, w: 280, h: 286 };
const GRAPHS: Rect = { x: 810, y: 684, w: 650, h: 286 };
const EMAIL: Rect = { x: 1520, y: 236, w: 320, h: 388 };
const CLAUDE: Rect = { x: 1520, y: 684, w: 320, h: 150 };
const OPENAI: Rect = { x: 1520, y: 852, w: 320, h: 118 };

function Connectors() {
  const line = "var(--line-strong)";
  const lit = "rgb(110 165 255 / 0.55)";
  return (
    <svg aria-hidden="true" className="pointer-events-none absolute inset-0" width={1920} height={1080}>
      <defs>
        <marker id="a" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
          <path d="M0 0 8 4 0 8z" fill="rgb(255 255 255 / 0.32)" />
        </marker>
        <marker id="b" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
          <path d="M0 0 8 4 0 8z" fill={lit} />
        </marker>
      </defs>
      <g fill="none" strokeWidth={1.5}>
        {/* browser -> API */}
        <path d="M240 414 V476" stroke={line} markerEnd="url(#a)" />
        {/* API -> Temporal: start, signal, query */}
        <path d="M400 565 H470" stroke={lit} markerEnd="url(#b)" />
        {/* API -> Postgres: reads cases */}
        <path d="M240 654 V752" stroke={line} markerEnd="url(#a)" />
        {/* Temporal activities -> rule engine, agents */}
        <path d="M610 624 V684" stroke={lit} markerEnd="url(#b)" />
        <path d="M1135 624 V684" stroke={lit} markerEnd="url(#b)" />
        {/* Temporal activities -> Postgres (along the bottom) */}
        <path d="M780 624 V1012 H240 V958" stroke={line} markerEnd="url(#a)" />
        {/* Temporal activities -> EmailChannel */}
        <path d="M1460 430 H1520" stroke={lit} markerEnd="url(#b)" />
        {/* agents -> models (OpenAI only as fallback) */}
        <path d="M1460 759 H1520" stroke={line} markerEnd="url(#a)" />
        <path d="M1460 911 H1520" stroke={line} strokeDasharray="4 5" markerEnd="url(#a)" />
      </g>
    </svg>
  );
}

function Label({ x, y, children, className }: { x: number; y: number; children: ReactNode; className?: string }) {
  return (
    <span className={cn("absolute text-[14px] whitespace-nowrap text-text-3", className)} style={{ left: x, top: y }}>
      {children}
    </span>
  );
}

export function ArchitectureSlide() {
  return (
    <SlideFrame>
      <header className="absolute top-[64px] left-[80px] w-[1760px]">
        <h2 className="text-[54px] leading-none font-[620] tracking-[-0.035em] [font-stretch:88%]">How Recourse is built</h2>
        <p className="mt-4 max-w-[60ch] text-[22px] leading-snug text-text-2">
          One durable workflow per case, short reasoning graphs inside it, and every amount and date
          from a deterministic rule engine.
        </p>
      </header>

      <Connectors />
      <Label x={252} y={436}>REST</Label>
      <Label x={252} y={692}>reads cases</Label>
      <Label x={622} y={642}>assess</Label>
      <Label x={1147} y={642}>intake, draft, judge reply</Label>
      <Label x={290} y={984}>writes cases, drafts and the audit log</Label>

      {/* Browser and API ---------------------------------------------------------------- */}
      <Box r={FRONT}>
        <Head logos={[["/logos/nextdotjs.svg", "Next.js"], ["/logos/vercel.svg", "Vercel"]]} title="Next.js" sub="on Vercel" />
        <p className="mt-4 text-[16px] leading-snug text-text-2">Landing page and the case app. Every action becomes a signal.</p>
      </Box>
      <Box r={API}>
        <Head logos={[["/logos/fastapi.svg", "FastAPI"]]} title="FastAPI" sub="Pydantic v2" />
        <p className="mt-4 text-[16px] leading-snug text-text-2">Uploads, case signals and queries, Gmail sign-in (OAuth).</p>
      </Box>
      <Box r={DB}>
        <Head logos={[["/logos/neon.svg", "Neon"], ["/logos/postgresql.svg", "PostgreSQL"]]} title="Neon Postgres" />
        <p className="mt-4 text-[16px] leading-snug text-text-2">Cases, evidence, drafts, emails and replies.</p>
        <p className="mt-1.5 text-[16px] leading-snug text-text-2">Append-only audit log of every step.</p>
      </Box>

      {/* Temporal --------------------------------------------------------------------- */}
      <Box r={TEMPORAL} lit>
        <div className="flex items-start justify-between gap-6">
          <Head logos={[["/logos/temporal.svg", "Temporal"]]} title="Temporal: CaseWorkflow" sub="One per case. Runs for weeks and survives restarts." />
        </div>
        <ol className="mt-5 flex items-center gap-x-1">
          {STATES.map((s, i) => (
            <li key={s} className="flex items-center gap-1">
              {i > 0 ? <span aria-hidden="true" className="text-[14px] text-text-3">›</span> : null}
              <span className="rounded-full border border-line-strong px-2.5 py-1 text-[14px] whitespace-nowrap text-text-2">{s}</span>
            </li>
          ))}
        </ol>
        <div className="mt-6 grid grid-cols-3 gap-8 border-t border-line pt-5">
          <List title="Signals" items={["Approve or reject a draft", "Bank replied", "Escalate", "Filed, closed, abandoned"]} />
          <List
            title="Durable timers"
            items={["Bank's reply window (30 days or more)", "Ombudsman 90-day filing window", "Gmail thread check"]}
            note="Demo mode: one day lasts 60 seconds"
          />
          <List
            title="Activities"
            items={["Intake, assess, draft", "Send email, check the thread", "Judge a reply, record each step"]}
            note="Idempotent, with timeouts and retries"
          />
        </div>
      </Box>

      {/* Rule engine ------------------------------------------------------------------ */}
      <Box r={RULES}>
        <h3 className="text-[22px] leading-tight font-[600] tracking-[-0.01em] [font-stretch:92%]">Rule engine</h3>
        <p className="text-[15px] text-text-3">Plain Python, no model</p>
        <div className="mt-5 flex flex-col gap-2.5 text-[16px] leading-snug">
          <span className="rounded-[0.9rem] border border-line px-3.5 py-2.5 text-text-2">Versioned RBI rulebook, each value quoting its source</span>
          <span aria-hidden="true" className="ml-5 h-3 w-px bg-line-strong" />
          <span className="rounded-[0.9rem] border border-blue/50 bg-blue/10 px-3.5 py-2.5 text-text">Amount owed, deadlines, citations</span>
        </div>
      </Box>

      {/* LangGraph agents ---------------------------------------------------------------- */}
      <Box r={GRAPHS}>
        <Head logos={[["/logos/langgraph.svg", "LangGraph"]]} title="LangGraph agents" sub="Short reasoning runs inside activities" />
        <div className="mt-5 grid grid-cols-3 gap-6">
          <div>
            <h4 className="mb-2.5 text-[16px] font-[560] text-text">IntakeGraph</h4>
            <Steps items={["classify", "extract", "validate"]} />
          </div>
          <div>
            <h4 className="mb-2.5 text-[16px] font-[560] text-text">DraftGraph</h4>
            <Steps items={["write", "verify (mandatory)", "accept or fail closed"]} accent="verify (mandatory)" />
          </div>
          <div>
            <h4 className="mb-2.5 text-[16px] font-[560] text-text">ReplyJudgeGraph</h4>
            <Steps items={["phishing check", "classify (no tools)", "verdict in code"]} />
          </div>
        </div>
      </Box>

      {/* Email channel ------------------------------------------------------------------ */}
      <Box r={EMAIL}>
        <h3 className="text-[22px] leading-tight font-[600] tracking-[-0.01em] [font-stretch:92%]">EmailChannel</h3>
        <p className="text-[15px] text-text-3">Sandbox by default</p>
        <div className="mt-5 flex flex-col gap-3">
          <div className="rounded-[1rem] border border-line p-4">
            <div className="flex items-center gap-3">
              <LogoTile src="/logos/gmail.svg" name="Gmail" size={38} />
              <span className="text-[17px] font-[560]">GmailChannel</span>
            </div>
            <p className="mt-2.5 text-[15px] leading-snug text-text-2">Sends after your approval, then watches only the case&apos;s own thread.</p>
          </div>
          <div className="rounded-[1rem] border border-line p-4">
            <span className="text-[17px] font-[560]">ManualChannel</span>
            <p className="mt-1.5 text-[15px] leading-snug text-text-2">Copy or mailto to send; paste the bank&apos;s reply. Always works.</p>
          </div>
        </div>
      </Box>

      {/* Models --------------------------------------------------------------------------- */}
      <Box r={CLAUDE}>
        <Head logos={[["/logos/claude.svg", "Claude"]]} title="Claude" />
        <p className="mt-3 text-[15px] leading-snug text-text-2">Sonnet: drafting</p>
        <p className="text-[15px] leading-snug text-text-2">Haiku: extraction, reply labels</p>
      </Box>
      <Box r={OPENAI}>
        <Head logos={[["/logos/openai.svg", "OpenAI"]]} title="OpenAI" sub="Fallback if Claude is unavailable" />
      </Box>

      <p className="absolute right-[80px] bottom-[28px] text-[13px] text-text-3">Logos are trademarks of their respective owners.</p>
    </SlideFrame>
  );
}
