"use client";

import { motion, useReducedMotion } from "motion/react";

import type { CaseView } from "@/lib/api";
import { caseToday, complaintDay, dayNumber, dayToIso, elapsed, paymentDay, windows } from "@/lib/case-progress";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

/*
 * The case on one date axis: each rule window as a bar (the part already gone filled in),
 * compensation building up after a missed reversal, what happened as dots, and today.
 */

const EASE = [0.22, 1, 0.36, 1] as const;

type Mark = { day: number; label: string };

function milestones(c: CaseView): Mark[] {
  const out: Mark[] = [];
  const paid = paymentDay(c);
  if (paid !== null) out.push({ day: paid, label: "Payment" });
  const sent = complaintDay(c);
  if (sent !== null && (c.email?.status === "SENT" || c.bank_complained_on)) out.push({ day: sent, label: "Letter sent" });
  const firstReply = c.timeline.find((e) => e.event_type === "reply.received");
  if (firstReply) out.push({ day: dayNumber(firstReply.occurred_at), label: "Bank replied" });
  if (c.ombudsman_filed_at) out.push({ day: dayNumber(c.ombudsman_filed_at), label: "Ombudsman filed" });
  const end = c.timeline.find((e) => ["case.resolved", "case.closed", "case.abandoned"].includes(e.event_type));
  if (end) {
    out.push({
      day: dayNumber(end.occurred_at),
      label: end.event_type === "case.resolved" ? "Resolved" : end.event_type === "case.closed" ? "Closed" : "Stopped",
    });
  }
  return out.sort((a, b) => a.day - b.day);
}

/** Labels that would overlap go on a lower line (greedy, two or three levels). */
function levels(marks: Mark[], pos: (d: number) => number): number[] {
  const lastEnd: number[] = [];
  return marks.map((m) => {
    const x = pos(m.day);
    let lvl = lastEnd.findIndex((e) => x - e > 14);
    if (lvl === -1) lvl = lastEnd.length;
    lastEnd[lvl] = x;
    return lvl;
  });
}

/** Keep a label inside the track at its edges: centred normally, left- or right-aligned near the ends. */
function edge(x: number): string {
  return x < 6 ? "translate-x-0" : x > 94 ? "-translate-x-full" : "-translate-x-1/2";
}

function ticks(min: number, max: number, n = 5): number[] {
  const step = Math.max(1, Math.round((max - min) / (n - 1)));
  const out: number[] = [];
  for (let d = min; d <= max; d += step) out.push(d);
  return out;
}

function Row({
  label,
  sub,
  todayX,
  children,
  className,
}: {
  label: string;
  sub?: string;
  todayX: number;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-[11rem_minmax(0,1fr)] sm:gap-4">
      <div className="pt-2.5 sm:py-3">
        <div className="text-[0.88rem] leading-tight text-text">{label}</div>
        {sub ? <div className="figures text-[0.78rem] text-text-3">{sub}</div> : null}
      </div>
      <div className={cn("relative", className)}>
        <span
          aria-hidden="true"
          className="absolute inset-y-0 w-px border-l border-dashed border-blue-bright/50"
          style={{ left: `${todayX}%` }}
        />
        {children}
      </div>
    </div>
  );
}

export function CaseChart({ c }: { c: CaseView }) {
  const still = useReducedMotion() === true;
  const today = caseToday(c);
  const ws = windows(c).sort((a, b) => a.start - b.start || a.end - b.end);
  const marks = milestones(c);

  const accruing = c.entitlement?.money.some((m) => m.accruing);
  const reversal = ws.find((w) => w.key === "reversal_deadline");
  const accrue = accruing && reversal && today > reversal.end ? { start: reversal.end, end: today } : null;

  if (ws.length === 0 && marks.length < 2) {
    return <p className="text-sm text-text-3">The graph appears once the case has been assessed.</p>;
  }

  const days = [...ws.flatMap((w) => [w.start, w.end]), ...marks.map((m) => m.day), today];
  const span = Math.max(...days) - Math.min(...days);
  const pad = Math.max(1, Math.round(span * 0.04));
  const min = Math.min(...days) - pad;
  const max = Math.max(...days) + pad;
  const pos = (d: number) => ((d - min) / (max - min)) * 100;
  const markLevels = levels(marks, pos);
  const markRows = Math.max(1, ...markLevels.map((l) => l + 1));
  const todayX = pos(today);

  return (
    <figure aria-label="Case timeline graph" className="flex flex-col">
      {ws.map((w, i) => {
        const left = pos(w.start);
        const width = Math.max(pos(w.end) - left, 0.8);
        const passed = w.end < today;
        const used = elapsed(w, today);
        return (
          <Row todayX={todayX} key={w.key} label={w.name} sub={`by ${formatDate(w.line.due)}`} className="h-9 sm:h-auto sm:min-h-12">
            <div className="absolute top-1/2 h-2.5 -translate-y-1/2" style={{ left: `${left}%`, width: `${width}%` }}>
              <motion.div
                className={cn(
                  "relative h-full origin-left overflow-hidden rounded-full border",
                  passed ? "border-white/10 bg-white/[0.06]" : "border-blue/40 bg-blue/[0.12]",
                )}
                initial={still ? false : { scaleX: 0 }}
                animate={{ scaleX: 1 }}
                transition={{ duration: 0.9, delay: 0.1 + i * 0.1, ease: EASE }}
              >
                <div
                  className={cn("h-full origin-left rounded-full", passed ? "bg-white/20" : "bg-blue")}
                  style={{ transform: `scaleX(${used})` }}
                />
              </motion.div>
            </div>
          </Row>
        );
      })}

      {accrue ? (
        <Row todayX={todayX} label="₹100 a day building up" sub={`since ${formatDate(dayToIso(accrue.start))}`} className="h-9 sm:h-auto sm:min-h-12">
          <motion.div
            className="absolute top-1/2 h-2.5 origin-left -translate-y-1/2 rounded-full bg-[repeating-linear-gradient(135deg,var(--blue-bright)_0_4px,transparent_4px_8px)] opacity-80"
            style={{ left: `${pos(accrue.start)}%`, width: `${Math.max(pos(accrue.end) - pos(accrue.start), 0.8)}%` }}
            initial={still ? false : { scaleX: 0 }}
            animate={{ scaleX: 1 }}
            transition={{ duration: 0.9, delay: 0.1 + ws.length * 0.1, ease: EASE }}
          />
        </Row>
      ) : null}

      {/* what happened */}
      <Row todayX={todayX} label="What happened" className="mt-1 border-t border-line">
        <div style={{ height: `${1.25 + markRows * 1.35}rem` }}>
          <span aria-hidden="true" className="absolute top-[0.85rem] right-0 left-0 h-px bg-line-strong" />
          {marks.map((m, i) => (
            <div key={m.label + m.day} className="absolute top-[0.6rem]" style={{ left: `${pos(m.day)}%` }}>
              <span className="absolute size-2.5 -translate-x-1/2 rounded-full border-2 border-ink bg-text ring-1 ring-text-2" />
              <span
                className={cn("absolute top-2.5 text-[0.72rem] whitespace-nowrap text-text-2", edge(pos(m.day)))}
                style={{ marginTop: `${0.25 + markLevels[i] * 1.35}rem` }}
                title={formatDate(dayToIso(m.day))}
              >
                {m.label}
              </span>
            </div>
          ))}
        </div>
      </Row>

      {/* date axis */}
      <div className="grid grid-cols-1 sm:grid-cols-[11rem_minmax(0,1fr)] sm:gap-4">
        <div className="hidden sm:block" />
        <div className="relative h-8 border-t border-line">
          {ticks(min, max).map((d) => (
            <span
              key={d}
              className={cn("figures absolute top-1.5 text-[0.7rem] whitespace-nowrap text-text-3", edge(pos(d)))}
              style={{ left: `${pos(d)}%` }}
            >
              {formatDate(dayToIso(d)).replace(/ \d{4}$/, "")}
            </span>
          ))}
          <span
            className="absolute -top-px h-2 w-px bg-blue-bright"
            style={{ left: `${todayX}%` }}
            aria-hidden="true"
          />
        </div>
      </div>

      <figcaption className="mt-2 flex flex-wrap gap-x-5 gap-y-1.5 text-[0.78rem] text-text-3">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-5 rounded-full border border-blue/40 bg-blue/[0.12]" /> Time the rules allow
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-5 rounded-full bg-blue" /> Already gone
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-3 border-l border-dashed border-blue-bright/70" /> Today, {formatDate(dayToIso(today))}
        </span>
      </figcaption>
    </figure>
  );
}
