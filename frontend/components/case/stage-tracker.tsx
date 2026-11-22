"use client";

import { Check } from "lucide-react";

import type { CaseView } from "@/lib/api";
import { nextStep, OWNER_LABEL, STAGES, stageStates } from "@/lib/case-progress";
import { cn } from "@/lib/utils";

/** The case's six stages, always in the same place: done, current (lit) and still to come. */
export function StageTracker({ c }: { c: CaseView }) {
  const states = stageStates(c);
  const { owner } = nextStep(c);
  return (
    <nav aria-label="Case progress" className="glass rounded-[1.25rem] px-4 py-4 sm:px-6 sm:py-5">
      <ol className="grid grid-cols-6">
        {STAGES.map((s, i) => {
          const st = states[i];
          return (
            <li
              key={s.key}
              aria-current={st === "current" ? "step" : undefined}
              className="relative flex flex-col items-center text-center"
            >
              {/* connector to the previous stage */}
              {i > 0 ? (
                <span
                  aria-hidden="true"
                  className={cn(
                    "absolute top-[0.8rem] right-1/2 left-[-50%] h-px",
                    st === "done" || st === "current" ? "bg-blue/70" : "bg-line-strong",
                    st === "skipped" && "h-0 border-t border-dashed border-line-strong bg-transparent",
                  )}
                />
              ) : null}
              <span
                className={cn(
                  "relative z-10 grid size-[1.6rem] place-items-center rounded-full border text-[0.75rem] transition-colors duration-700",
                  st === "done" && "border-blue bg-blue text-white",
                  st === "current" && "border-blue bg-ink text-blue-bright shadow-[0_0_0_5px_rgb(59_130_246/0.16)]",
                  st === "upcoming" && "border-line-strong bg-ink text-text-3",
                  st === "skipped" && "border-dashed border-line-strong bg-ink text-text-3",
                )}
              >
                {st === "done" ? (
                  <Check className="size-3.5" strokeWidth={3} aria-hidden="true" />
                ) : st === "current" ? (
                  <span className="size-2 rounded-full bg-blue" aria-hidden="true" />
                ) : (
                  <span className="figures">{i + 1}</span>
                )}
              </span>
              <span
                className={cn(
                  "mt-2 hidden text-[0.8rem] leading-tight sm:block",
                  st === "current" ? "font-[560] text-text" : st === "done" ? "text-text-2" : "text-text-3",
                )}
              >
                {s.label}
              </span>
              {st === "current" ? (
                <span className="mt-0.5 hidden text-[0.72rem] text-blue-bright sm:block">{OWNER_LABEL[owner]}</span>
              ) : null}
              <span className="sr-only">
                {st === "done" ? ", done" : st === "current" ? `, now: ${OWNER_LABEL[owner]}` : st === "skipped" ? ", not needed" : ", to come"}
              </span>
            </li>
          );
        })}
      </ol>
      {/* phones: the current stage in words under the dots */}
      <p className="mt-3 text-center text-sm sm:hidden">
        <span className="text-text">{STAGES[states.includes("current") ? states.indexOf("current") : 5].label}</span>
        <span className="text-text-3">: {OWNER_LABEL[owner]}</span>
      </p>
    </nav>
  );
}
