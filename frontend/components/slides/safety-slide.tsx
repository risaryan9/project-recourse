import { Check, FileCheck2, Lock, Mail, ShieldAlert } from "lucide-react";

import { SlideFrame } from "@/components/slides/frame";
import { cn } from "@/lib/utils";

/* Each guard is true in the current code (rule engine + verify, phishing screen + reply judge,
   PII redaction + token encryption, thread-only Gmail + per-email approval). */
const CARDS = [
  {
    icon: FileCheck2,
    threat: "Made-up numbers",
    guard: "Every amount and date comes from the rule engine, and each draft is checked against it.",
    proof: (
      <>
        <Check className="size-4 text-blue-bright" strokeWidth={2.5} aria-hidden="true" />
        <span className="figures">₹2,450.00</span> matches the rule
      </>
    ),
  },
  {
    icon: ShieldAlert,
    threat: "Prompt injection and phishing",
    guard: "Replies are screened for scams, and the reply judge has no tools: it can only pick a label.",
    proof: <>Label only, no actions</>,
  },
  {
    icon: Lock,
    threat: "Personal data leaks",
    guard: "Account numbers and codes in text and replies are masked before a model sees them. Tokens are encrypted.",
    proof: <span className="figures">A/c XXXXXXXX1234</span>,
  },
  {
    icon: Mail,
    threat: "Overreaching with your email",
    guard: "Recourse reads only the case's own thread, logs every call, and sends nothing you haven't approved.",
    proof: (
      <>
        Only thread <span className="figures">[RC-4MTJ]</span>
      </>
    ),
  },
];

export function SafetySlide() {
  return (
    <SlideFrame>
      <div className="flex h-full flex-col px-[96px] pt-[80px] pb-[64px]">
        <h2 className="text-[60px] leading-none font-[620] tracking-[-0.035em] [font-stretch:88%]">Safe by design</h2>
        <p className="mt-5 max-w-[48ch] text-[24px] leading-snug text-text-2">
          Each risk has a guard in the code, not a promise in a prompt.
        </p>

        <div className="mt-14 grid flex-1 grid-cols-2 grid-rows-2 gap-7">
          {CARDS.map((c, i) => {
            const Icon = c.icon;
            return (
              <article
                key={c.threat}
                className={cn("glass flex flex-col rounded-[1.75rem] px-11 py-10", i === 0 && "glass-lit")}
              >
                <span className="glass grid size-14 place-items-center rounded-[1rem]">
                  <Icon className="size-6 text-blue-bright" strokeWidth={1.8} aria-hidden="true" />
                </span>
                <h3 className="mt-7 text-[32px] leading-tight font-[600] tracking-[-0.02em] [font-stretch:92%]">{c.threat}</h3>
                <p className="mt-3 max-w-[44ch] text-[21px] leading-snug text-text-2">{c.guard}</p>
                <span className="mt-auto inline-flex w-fit items-center gap-2 rounded-full border border-blue/45 bg-blue/10 px-4 py-1.5 text-[16px] text-text">
                  {c.proof}
                </span>
              </article>
            );
          })}
        </div>

        <p className="mt-7 text-[18px] text-text-3">
          Every step is recorded in Temporal&apos;s workflow history and the case&apos;s audit log.
        </p>
      </div>
    </SlideFrame>
  );
}
