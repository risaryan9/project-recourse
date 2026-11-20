"use client";

import { motion, useInView, useReducedMotion } from "motion/react";
import { useRef } from "react";

/*
 * Showpiece 3: a "bank reply" that asks for an OTP is caught. Mirrors the product: the
 * deterministic screen flags requests for OTPs and PINs before any AI reads the message,
 * and nothing about the case changes.
 */

const EASE = [0.22, 1, 0.36, 1] as const;

export function ReplyCheck() {
  const ref = useRef<HTMLDivElement>(null);
  const seen = useInView(ref, { once: true, margin: "0px 0px -25% 0px" });
  const reduce = useReducedMotion();
  const on = reduce === true || seen;

  return (
    <div ref={ref} className="relative w-full max-w-[30rem]">
      <div className="glass rounded-[1.75rem] p-6 sm:p-7">
        <div className="flex items-baseline justify-between gap-4 border-b border-line pb-4">
          <div className="min-w-0">
            <div className="text-[0.95rem] text-text">Refund Desk</div>
            <div className="truncate text-sm text-text-3">refunds@bank-help.example</div>
          </div>
          <span className="figures shrink-0 text-sm text-text-3">29 Nov, 10:42</span>
        </div>
        <div className="space-y-3 pt-4 text-[0.95rem] leading-relaxed text-text-2">
          <p>Dear customer,</p>
          <p>Your refund of ₹2,950.00 has been approved and is ready for release.</p>
          <p>
            {/* A background underline sweeps across every line of the sentence in reading
                order (an absolutely placed line would only follow one line box). */}
            <motion.span
              className="bg-no-repeat [box-decoration-break:slice] [background-image:linear-gradient(var(--blue),var(--blue))] [background-position:0_100%] pb-0.5"
              initial={reduce ? false : { backgroundSize: "0% 2px" }}
              animate={{ backgroundSize: on ? "100% 2px" : "0% 2px" }}
              transition={{ duration: 1.4, delay: 0.3, ease: EASE }}
              style={reduce ? { backgroundSize: "100% 2px" } : undefined}
            >
              To release it, reply with the OTP you receive and your UPI PIN.
            </motion.span>
          </p>
        </div>
      </div>

      <motion.div
        role="status"
        className="glass glass-lit mt-4 rounded-[1.25rem] p-5 sm:ml-12"
        initial={reduce ? false : { opacity: 0, y: 10 }}
        animate={on ? { opacity: 1, y: 0 } : { opacity: 0, y: 10 }}
        transition={{ duration: 0.9, delay: 1.2, ease: EASE }}
      >
        <div className="text-[0.95rem] font-[560] text-text">This looks like a scam.</div>
        <p className="mt-1.5 text-sm leading-relaxed text-text-2">
          Banks never ask for your OTP or PIN. Nothing was sent, and your case is unchanged.
          Report it at cybercrime.gov.in or call 1930.
        </p>
      </motion.div>
    </div>
  );
}
