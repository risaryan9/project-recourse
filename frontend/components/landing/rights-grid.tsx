"use client";

import { animate, motion, useInView, useReducedMotion } from "motion/react";
import { useEffect, useRef, type ReactNode } from "react";

import { formatINR } from "@/lib/format";

/*
 * "The rules are already on your side": three situations, each with a small picture of the
 * rule (plain-language summaries of RBI rules, see CLAUDE.md §4), and a strip of key facts.
 * Each picture plays once when it scrolls into view; reduced motion shows the end state.
 */

const EASE = [0.22, 1, 0.36, 1] as const;

function useOnce<T extends Element>() {
  const ref = useRef<T>(null);
  const inView = useInView(ref, { once: true, margin: "0px 0px -15% 0px" });
  const still = useReducedMotion() === true;
  return { ref, play: inView || still, still };
}

/* UPI: a track from payment to the one-day deadline, then ₹100 for each late day. ----------- */

const UPI_LATE_DAYS = 4;

function UpiTrack() {
  const { ref, play, still } = useOnce<HTMLDivElement>();
  const owed = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!play || !owed.current) return;
    const el = owed.current;
    const final = formatINR(UPI_LATE_DAYS * 100, { paise: false });
    if (still) {
      el.textContent = final;
      return;
    }
    const a = animate(0, UPI_LATE_DAYS, {
      duration: 1.6,
      delay: 0.9,
      ease: "linear",
      onUpdate: (v) => {
        el.textContent = formatINR(Math.floor(v) * 100, { paise: false });
      },
      onComplete: () => {
        el.textContent = final;
      },
    });
    return () => a.stop();
  }, [play, still]);

  const nodes: { top: string; bottom: string; due?: boolean; late?: boolean }[] = [
    { top: "Paid", bottom: "Day 0" },
    { top: "Due", bottom: "Day 1", due: true },
    ...Array.from({ length: UPI_LATE_DAYS }, (_, i) => ({ top: "+₹100", bottom: `Day ${i + 2}`, late: true })),
  ];

  return (
    <div ref={ref} className="flex h-full flex-col justify-between">
      <div className="relative mt-1">
        {/* the line behind the nodes, drawn left to right */}
        <div className="absolute top-[1.75rem] right-[8.33%] left-[8.33%] h-px bg-line-strong" />
        <motion.div
          className="absolute top-[1.75rem] right-[8.33%] left-[8.33%] h-px origin-left bg-blue"
          initial={{ scaleX: 0 }}
          animate={{ scaleX: play ? 1 : 0 }}
          transition={{ duration: still ? 0 : 2.4, ease: EASE }}
        />
        <ol className="relative grid grid-cols-6">
          {nodes.map((n, i) => (
            <motion.li
              key={n.bottom}
              className="flex flex-col items-center gap-1.5"
              initial={{ opacity: 0, y: 4 }}
              animate={play ? { opacity: 1, y: 0 } : { opacity: 0, y: 4 }}
              transition={{ duration: still ? 0 : 0.6, delay: still ? 0 : 0.15 + i * 0.38, ease: EASE }}
            >
              <span
                className={
                  "h-4 whitespace-nowrap leading-4 " +
                  (n.late ? "figures text-[0.68rem] font-[560] text-blue-bright" : "text-[0.7rem] text-text-2")
                }
              >
                {n.top}
              </span>
              <span className="grid h-3 place-items-center">
              <span
                className={
                  n.due
                    ? "size-2.5 rounded-full bg-blue shadow-[0_0_0_4px_rgb(59_130_246/0.18)]"
                    : n.late
                      ? "size-2 rounded-full bg-blue-bright/80"
                      : "size-2 rounded-full bg-text-2"
                }
              />
              </span>
              <span className="whitespace-nowrap text-[0.66rem] text-text-3">{n.bottom}</span>
            </motion.li>
          ))}
        </ol>
      </div>
      <div className="flex items-baseline justify-between border-t border-line pt-3 text-[0.85rem]">
        <span className="text-text-3">Example: owed so far</span>
        <span ref={owed} className="figures font-[560] text-text">
          {formatINR(0, { paise: false })}
        </span>
      </div>
    </div>
  );
}

/* ATM: a week of days, the first five are the bank's window, then ₹100 a day. ------------ */

function AtmWeek() {
  const { ref, play, still } = useOnce<HTMLDivElement>();
  const days = Array.from({ length: 7 }, (_, i) => i + 1);
  return (
    <div ref={ref} className="flex h-full flex-col justify-between">
      <div className="grid grid-cols-7 gap-1.5">
        {days.map((d, i) => {
          const inWindow = d <= 5;
          return (
            <div key={d} className="flex flex-col items-center gap-1.5">
              <div
                className={
                  "relative grid aspect-square w-full place-items-center overflow-hidden rounded-[0.6rem] border " +
                  (inWindow ? "border-line" : "border-dashed border-line-strong")
                }
              >
                {inWindow ? (
                  <motion.span
                    aria-hidden="true"
                    className="absolute inset-0 origin-bottom bg-blue/25"
                    initial={{ scaleY: 0 }}
                    animate={{ scaleY: play ? 1 : 0 }}
                    transition={{ duration: still ? 0 : 0.5, delay: still ? 0 : 0.2 + i * 0.22, ease: EASE }}
                  />
                ) : (
                  <motion.span
                    className="figures relative text-[0.6rem] font-[560] tracking-[-0.02em] text-blue-bright"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: play ? 1 : 0 }}
                    transition={{ duration: still ? 0 : 0.6, delay: still ? 0 : 1.5 + (d - 6) * 0.3 }}
                  >
                    +₹100
                  </motion.span>
                )}
                {inWindow ? <span className="relative text-[0.72rem] text-text">{d}</span> : null}
              </div>
            </div>
          );
        })}
      </div>
      <div className="grid grid-cols-7 gap-1.5 text-[0.72rem]">
        <span className="col-span-5 border-t border-blue/50 pt-1.5 text-text-2">Bank&apos;s window to refund</span>
        <span className="col-span-2 border-t border-dashed border-line-strong pt-1.5 text-text-3">Late</span>
      </div>
      <div className="flex items-baseline justify-between border-t border-line pt-3 text-[0.85rem]">
        <span className="text-text-3">Then, for every day late</span>
        <span className="figures font-[560] text-text">₹100</span>
      </div>
    </div>
  );
}

/* Fraud: the reporting countdown Recourse shows, with ₹0 at its centre. ------------------ */

function FraudRing() {
  const { ref, play, still } = useOnce<HTMLDivElement>();
  return (
    <div ref={ref} className="flex h-full items-center gap-5">
      <div className="relative size-[6.5rem] shrink-0">
        <svg viewBox="0 0 100 100" className="size-full -rotate-90" aria-hidden="true">
          <circle cx="50" cy="50" r="44" fill="none" stroke="var(--line-strong)" strokeWidth="5" />
          <motion.circle
            cx="50"
            cy="50"
            r="44"
            fill="none"
            stroke="var(--blue)"
            strokeWidth="5"
            strokeLinecap="round"
            initial={{ pathLength: 1 }}
            animate={{ pathLength: play ? 0.62 : 1 }}
            transition={{ duration: still ? 0 : 2.2, delay: still ? 0 : 0.3, ease: EASE }}
          />
        </svg>
        <div className="absolute inset-0 grid place-content-center text-center">
          <span className="figures text-[1.6rem] leading-none font-[620] tracking-[-0.03em] text-text">₹0</span>
          <span className="mt-1 text-[0.66rem] text-text-3">your share</span>
        </div>
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2 text-[0.8rem]">
        <div className="flex items-center gap-2 whitespace-nowrap text-text">
          <span aria-hidden="true" className="relative flex size-2">
            {!still ? <span className="absolute inline-flex size-full animate-ping rounded-full bg-blue opacity-60 [animation-duration:2.4s]" /> : null}
            <span className="relative inline-flex size-2 rounded-full bg-blue" />
          </span>
          Report it now
        </div>
        <div className="rounded-[0.75rem] border border-line px-3 py-1.5 whitespace-nowrap text-text-2">
          Call <span className="figures text-text">1930</span>
        </div>
        <div className="truncate rounded-[0.75rem] border border-line px-3 py-1.5 text-text">
          cybercrime.gov.in
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------------------- */

const CARDS: { figure: string; title: string; body: string; visual: ReactNode }[] = [
  {
    figure: "1 day",
    title: "Your UPI transfer never arrived",
    body: "The money left your account but never reached the other person. Your bank has one day to reverse it, then owes you ₹100 for every day of delay.",
    visual: <UpiTrack />,
  },
  {
    figure: "5 days",
    title: "The ATM kept your cash",
    body: "Your account was debited but no notes came out. The bank has five days to put the money back, then ₹100 for every day it is late.",
    visual: <AtmWeek />,
  },
  {
    figure: "₹0",
    title: "Money left without you",
    body: "If the fault isn't yours and you report it in time, the rules can leave you owing nothing. Recourse shows the deadline that applies to you.",
    visual: <FraudRing />,
  },
];

const FACTS = [
  { figure: "1 to 5 days", label: "for your bank to reverse a failed payment, depending on how you paid" },
  { figure: "90 days", label: "to take an unresolved complaint to the RBI Ombudsman once the bank's time is up" },
  { figure: "₹30 lakh", label: "the most the Ombudsman can award for consequential loss, on top of your money" },
];

export function RightsGrid() {
  return (
    <>
      <div className="mt-12 grid gap-4 md:grid-cols-2 lg:grid-cols-3 sm:mt-14">
        {CARDS.map((c, i) => (
          <motion.article
            key={c.title}
            className="glass flex flex-col rounded-[1.5rem] p-5 sm:p-6"
            initial={{ opacity: 0, y: 14 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "0px 0px -10% 0px" }}
            transition={{ duration: 0.9, delay: i * 0.08, ease: EASE }}
          >
            <div className="h-[10rem] rounded-[1rem] border border-line bg-white/[0.02] p-4">{c.visual}</div>
            <div className="mt-6 flex items-baseline gap-3">
              <span className="figures text-[2rem] leading-none font-[620] tracking-[-0.04em] text-text [font-stretch:86%]">
                {c.figure}
              </span>
            </div>
            <h3 className="mt-3 text-[1.125rem] font-[600] tracking-[-0.01em] text-text">{c.title}</h3>
            <p className="mt-2 text-[0.95rem] leading-[1.55] text-text-2">{c.body}</p>
          </motion.article>
        ))}
      </div>

      <div className="glass mt-4 grid rounded-[1.5rem] sm:grid-cols-3">
        {FACTS.map((f, i) => (
          <div
            key={f.figure}
            className={
              "px-6 py-5 sm:py-6 " + (i > 0 ? "border-t border-line sm:border-t-0 sm:border-l" : "")
            }
          >
            <div className="figures text-[1.5rem] leading-none font-[620] tracking-[-0.03em] text-blue-bright [font-stretch:88%]">
              {f.figure}
            </div>
            <p className="mt-2 text-[0.9rem] leading-[1.5] text-text-2">{f.label}</p>
          </div>
        ))}
      </div>
    </>
  );
}
