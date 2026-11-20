"use client";

import {
  AnimatePresence,
  motion,
  useMotionValueEvent,
  useReducedMotion,
  useScroll,
} from "motion/react";
import { useRef, useState } from "react";

import { formatINR } from "@/lib/format";
import { cn } from "@/lib/utils";

/*
 * Showpiece 2: one case, carried from start to finish. The steps are a real sequence (the
 * product's workflow), so they are numbered. Dates follow the example case in the hero:
 * complaint sent 20 Nov 2026; the bank's window ends 20 Dec; the Ombudsman can be approached
 * from 21 Dec until 20 Mar 2027 (RB-IOS 2026, clause 10(1)(f)-(g)).
 */

type Step = { title: string; body: string };

const STEPS: Step[] = [
  {
    title: "Share what happened",
    body: "Paste the bank's SMS or upload a screenshot. Recourse reads the amount, date and reference, and asks you to check anything it is unsure of.",
  },
  {
    title: "See what you're owed",
    body: "The rules decide the amount, not a guess. Every figure comes with the RBI clause behind it.",
  },
  {
    title: "Approve the letter",
    body: "Recourse writes the complaint to your bank. Nothing is sent until you have read it and said yes.",
  },
  {
    title: "Recourse keeps watch",
    body: "It counts down the days your bank has to reply, and checks every reply against the rules.",
  },
  {
    title: "If the bank doesn't put it right",
    body: "Your complaint to the RBI Ombudsman is ready, with the deadline to file it. You stay in control of every step.",
  },
];

const EASE = [0.22, 1, 0.36, 1] as const;

/* ---------------------------------------------------------------------------------------- */
/* The case card, in each of its states                                                     */
/* ---------------------------------------------------------------------------------------- */

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-6 border-t border-line py-2.5 first:border-t-0">
      <span className="text-sm text-text-3">{label}</span>
      <span className="figures text-right text-[0.95rem] text-text">{value}</span>
    </div>
  );
}

function Status({ children, live = false }: { children: React.ReactNode; live?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-line px-3 py-1 text-sm text-text-2">
      <span
        className={cn("size-1.5 rounded-full", live ? "bg-blue" : "bg-text-3")}
        aria-hidden="true"
      />
      {children}
    </span>
  );
}

function CardBody({ step }: { step: number }) {
  switch (step) {
    case 0:
      return (
        <>
          <Status>Facts confirmed</Status>
          <div className="mt-5">
            <Field label="Payment" value="UPI transfer" />
            <Field label="Amount" value={formatINR(2450)} />
            <Field label="Date" value="14 Nov 2026, 18:05" />
            <Field label="Reference" value="6318 4520 7719" />
            <Field label="What happened" value="Debited, not credited" />
          </div>
        </>
      );
    case 1:
      return (
        <>
          <Status>Assessed against the rules</Status>
          <div className="mt-6 text-sm text-text-2">Owed to you</div>
          <div className="figures mt-1 text-5xl leading-none font-[620] tracking-[-0.04em] text-blue-bright [font-stretch:88%]">
            {formatINR(2950)}
          </div>
          <div className="mt-6">
            <Field label="Reversal" value={formatINR(2450)} />
            <Field label="Compensation, 5 days late" value={formatINR(500)} />
          </div>
          <p className="mt-4 text-sm text-text-3">RBI/2019-20/67, Annex, Sl. 4(a)</p>
        </>
      );
    case 2:
      return (
        <>
          <Status>Letter ready for you</Status>
          <div className="mt-5 space-y-2.5 text-[0.95rem] leading-relaxed text-text-2">
            <p>To the Grievance Redressal Officer,</p>
            <p>
              My UPI transfer of <span className="figures text-text">{formatINR(2450)}</span> on
              14 November 2026 was debited but never credited. Under Annex, Sl. 4(a) of
              RBI/2019-20/67 it had to be reversed by 15 November 2026…
            </p>
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-2">
            <span className="inline-flex h-9 items-center rounded-full bg-blue px-4 text-sm font-[560] whitespace-nowrap text-white">
              Approve and send
            </span>
            <span className="text-sm text-text-3">Nothing is sent before this</span>
          </div>
        </>
      );
    case 3:
      return (
        <>
          <Status live>Waiting for the bank</Status>
          <div className="mt-6 flex items-baseline justify-between gap-4">
            <span className="text-sm text-text-2">Bank&apos;s window ends</span>
            <span className="figures text-text">20 Dec 2026</span>
          </div>
          <div
            className="mt-3 h-1 overflow-hidden rounded-full bg-white/[0.07]"
            role="img"
            aria-label="9 of 30 days used"
          >
            <div className="h-full w-[30%] rounded-full bg-blue" />
          </div>
          <div className="mt-6 rounded-2xl border border-line p-4">
            <div className="flex items-baseline justify-between gap-4">
              <span className="text-sm text-text">Reply from your bank</span>
              <span className="figures text-sm text-text-3">29 Nov</span>
            </div>
            <p className="mt-2 text-sm leading-relaxed text-text-2">
              Checked: an acknowledgement, not a decision. Complaint number saved. The deadline
              still stands.
            </p>
          </div>
        </>
      );
    default:
      return (
        <>
          <Status live>Ready for the RBI Ombudsman</Status>
          <p className="mt-5 text-[0.95rem] leading-relaxed text-text-2">
            The bank did not put it right in time. Your complaint is drafted, with every figure
            and its rule.
          </p>
          <div className="mt-5">
            {/* 15 Nov to 21 Dec = 36 days late: 2,450 + 36 × 100. */}
            <Field label="Owed on 21 Dec 2026" value={formatINR(6050)} />
            <Field label="You can file from" value="21 Dec 2026" />
            <Field label="Last day to file" value="20 Mar 2027" />
          </div>
          <p className="mt-4 text-sm text-text-3">
            You file it yourself at cms.rbi.org.in. Recourse prepares every field.
          </p>
        </>
      );
  }
}

function CaseCard({ step, className }: { step: number; className?: string }) {
  const reduce = useReducedMotion();
  return (
    <div className={cn("glass glass-lit rounded-[1.75rem] p-6 sm:p-7", className)}>
      <div className="mb-5 flex items-center justify-between gap-4">
        <span className="figures text-sm text-text-2">Case RC-4JJG</span>
        <span className="text-sm text-text-3">UPI transfer</span>
      </div>
      {/* A steady height, so the pinned card doesn't change size as the case moves on. */}
      <div className="relative min-h-[17.5rem] lg:min-h-[21rem]">
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={step}
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? undefined : { opacity: 0, y: -6 }}
            transition={{ duration: 0.55, ease: EASE }}
          >
            <CardBody step={step} />
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------------------- */
/* The section                                                                              */
/* ---------------------------------------------------------------------------------------- */

export function CaseStory() {
  const track = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(0);
  const { scrollYProgress } = useScroll({ target: track, offset: ["start start", "end end"] });

  useMotionValueEvent(scrollYProgress, "change", (p) => {
    const next = Math.min(STEPS.length - 1, Math.max(0, Math.floor(p * STEPS.length)));
    setActive((cur) => (cur === next ? cur : next));
  });

  return (
    <section aria-labelledby="how-title" className="relative">
      <div className="mx-auto max-w-6xl px-6 pt-28 sm:pt-36">
        <h2 id="how-title" className="type-title max-w-[18ch]">
          One case, carried all the way through.
        </h2>
        <p className="type-lede mt-5 max-w-[46ch]">
          From the moment you share what happened to the day the money is back, every step is
          visible, and every decision is yours.
        </p>
      </div>

      {/* Desktop: the card stays while the case moves forward with your scroll. */}
      <div ref={track} className="relative hidden lg:block" style={{ height: `${STEPS.length * 55}vh` }}>
        <div className="sticky top-0 flex h-screen items-center">
          <div className="mx-auto grid w-full max-w-6xl grid-cols-[1fr_27rem] gap-20 px-6">
            <ol className="flex flex-col justify-center gap-7">
              {STEPS.map((s, i) => (
                <li
                  key={s.title}
                  className={cn(
                    "grid grid-cols-[2.25rem_1fr] transition-opacity duration-700 ease-[var(--ease-calm)]",
                    i === active ? "opacity-100" : "opacity-35",
                  )}
                  aria-current={i === active ? "step" : undefined}
                >
                  <span
                    className={cn(
                      "figures pt-0.5 text-[0.95rem] transition-colors duration-700",
                      i === active ? "text-blue-bright" : "text-text-3",
                    )}
                  >
                    {i + 1}
                  </span>
                  <div>
                    <h3 className="type-heading">{s.title}</h3>
                    <p
                      className={cn(
                        "mt-2 max-w-[44ch] text-text-2 transition-[opacity] duration-700",
                        i === active ? "opacity-100" : "opacity-0",
                      )}
                    >
                      {s.body}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
            <CaseCard step={active} className="self-center" />
          </div>
        </div>
      </div>

      {/* Phones and tablets: no pinning; each step shows its own state of the case. */}
      <ol className="mx-auto mt-14 flex max-w-xl flex-col gap-16 px-6 lg:hidden">
        {STEPS.map((s, i) => (
          <li key={s.title}>
            <div className="grid grid-cols-[2rem_1fr]">
              <span className="figures pt-0.5 text-blue-bright">{i + 1}</span>
              <div>
                <h3 className="type-heading">{s.title}</h3>
                <p className="mt-2 text-text-2">{s.body}</p>
              </div>
            </div>
            <CaseCard step={i} className="mt-6" />
          </li>
        ))}
      </ol>
    </section>
  );
}
