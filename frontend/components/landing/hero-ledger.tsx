"use client";

import { animate, motion, useReducedMotion } from "motion/react";
import { useEffect, useRef } from "react";

import { formatINR } from "@/lib/format";

/*
 * Showpiece 1: what you're owed comes into being.
 * A synthetic example case (the same figures as the rule engine produces for it): a UPI
 * transfer of ₹2,450 on 14 Nov 2026, due back by 15 Nov (T+1), assessed on 20 Nov, so
 * 5 days late at ₹100 a day. Plays once (~2.6 s), then stays still.
 */

const DEBIT = 2450;
const PER_DAY = 100;
const DAYS_LATE = 5;
const TOTAL = DEBIT + PER_DAY * DAYS_LATE;

const EASE = [0.22, 1, 0.36, 1] as const;

function Row({
  label,
  detail,
  children,
  delay,
  still,
}: {
  label: string;
  detail?: React.ReactNode;
  children: React.ReactNode;
  delay: number;
  still: boolean;
}) {
  return (
    <motion.div
      className="flex items-baseline justify-between gap-6 py-3"
      initial={still ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.9, delay, ease: EASE }}
    >
      <div className="min-w-0">
        <div className="text-[0.95rem] text-text">{label}</div>
        {detail ? <div className="mt-0.5 text-sm text-text-3">{detail}</div> : null}
      </div>
      <div className="figures shrink-0 text-[1.05rem] text-text">{children}</div>
    </motion.div>
  );
}

export function HeroLedger() {
  const reduce = useReducedMotion();
  const still = reduce === true;
  const days = useRef<HTMLSpanElement>(null);
  const comp = useRef<HTMLSpanElement>(null);
  const total = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const set = (el: HTMLSpanElement | null, text: string) => {
      if (el) el.textContent = text;
    };
    // Tabular digits only while counting (no jitter); proportional figures once settled.
    const settle = (el: HTMLSpanElement | null) => el?.classList.remove("figures-tabular");
    if (still) {
      // The server renders the counting style (it cannot know the motion preference).
      set(days.current, String(DAYS_LATE));
      set(comp.current, formatINR(PER_DAY * DAYS_LATE));
      set(total.current, formatINR(TOTAL));
      settle(comp.current);
      settle(total.current);
      return;
    }
    // Days of delay tick up one by one; the compensation follows them exactly.
    const a = animate(0, DAYS_LATE, {
      duration: 1.1,
      delay: 1.0,
      ease: "easeOut",
      onUpdate: (v) => {
        const d = Math.round(v);
        set(days.current, String(d));
        set(comp.current, formatINR(PER_DAY * d));
      },
      onComplete: () => settle(comp.current),
    });
    const b = animate(0, TOTAL, {
      duration: 1.3,
      delay: 1.45,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => set(total.current, formatINR(Math.round(v))),
      onComplete: () => settle(total.current),
    });
    return () => {
      a.stop();
      b.stop();
    };
  }, [still]);

  return (
    <motion.figure
      aria-label={`Example: ${formatINR(TOTAL)} owed for a failed UPI transfer of ${formatINR(DEBIT)}`}
      className="glass glass-lit relative w-full max-w-[27rem] rounded-[1.75rem] p-6 sm:p-7"
      initial={still ? false : { opacity: 0, y: 18, scale: 0.985 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 1.2, delay: 0.15, ease: EASE }}
    >
      <div className="flex items-center justify-between gap-4">
        <span className="text-sm text-text-2">Example case</span>
        <span className="text-sm text-text-3">20 Nov 2026</span>
      </div>

      <div className="mt-5 border-t border-line">
        <Row
          label="UPI transfer, never credited"
          detail="14 Nov 2026, 18:05"
          delay={0.45}
          still={still}
        >
          {formatINR(DEBIT)}
        </Row>
      </div>

      <div className="border-t border-line">
        <Row label="Reversal of the debit" delay={0.75} still={still}>
          {formatINR(DEBIT)}
        </Row>
        <Row
          label="Compensation for delay"
          detail={
            <span className="figures">
              ₹100 × <span ref={days}>0</span> days late
            </span>
          }
          delay={0.95}
          still={still}
        >
          <span ref={comp} className="figures-tabular">
            {formatINR(0)}
          </span>
        </Row>
      </div>

      <motion.div
        className="mt-1 border-t border-line pt-5"
        initial={still ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 1, delay: 1.35, ease: EASE }}
      >
        <div className="text-sm text-text-2">Owed to you</div>
        <div className="figures mt-1 text-[clamp(2.6rem,2rem+2.4vw,3.4rem)] leading-none font-[620] tracking-[-0.04em] text-blue-bright [font-stretch:88%]">
          <span ref={total} className="figures-tabular">
            {formatINR(0)}
          </span>
        </div>
      </motion.div>

      <motion.figcaption
        className="mt-5 text-sm leading-relaxed text-text-3"
        initial={still ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 1.2, delay: 2.5, ease: EASE }}
      >
        RBI circular RBI/2019-20/67, Annex, Sl. 4(a): reverse within one day of the transfer,
        then ₹100 for every day of delay.
      </motion.figcaption>
    </motion.figure>
  );
}
