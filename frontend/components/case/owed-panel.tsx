"use client";

import { Notice, Panel } from "@/components/app/ui";
import type { CaseView, Citation, DeadlineLine } from "@/lib/api";
import { daysFromToday, formatDate, inr } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The clause behind a figure, linked to the RBI's own page. */
export function Cite({ c, className }: { c: Citation; className?: string }) {
  const ref = c.doc_ref.split(",")[0].trim();
  return (
    <a
      href={c.url}
      target="_blank"
      rel="noreferrer"
      title={`${c.doc_title}: "${c.quotes.join(" / ")}"`}
      className={cn(
        "text-sm text-text-3 underline decoration-line-strong underline-offset-4 transition-colors hover:text-text-2",
        className,
      )}
    >
      {c.para}, {ref}
    </a>
  );
}

function Deadline({ d }: { d: DeadlineLine }) {
  const days = daysFromToday(d.due);
  return (
    <li className="flex flex-col gap-0.5 py-3">
      <div className="flex items-baseline justify-between gap-4">
        <span className={cn("text-[0.95rem]", d.passed ? "text-text-3" : "text-text")}>{d.label}</span>
        <span className="figures shrink-0 text-[0.95rem] text-text">{formatDate(d.due)}</span>
      </div>
      <div className="flex items-baseline justify-between gap-4">
        <Cite c={d.citation} />
        <span className="shrink-0 text-sm text-text-3">
          {d.passed ? "passed" : days === 0 ? "today" : `in ${days} days`}
          {d.approximate ? ", approx." : ""}
        </span>
      </div>
    </li>
  );
}

const NOT_COVERED: Record<string, string> = {
  CUSTOMER_ERROR:
    "Money sent to the wrong person by mistake isn't covered by these rules. Contact your bank directly; it may ask the other bank to return it.",
  NOT_DOMESTIC: "These rules cover payments within India only.",
  BEFORE_FRAMEWORK: "This payment is older than the rules Recourse works with.",
  NO_MATCHING_RULE: "The RBI's list of failed payments has no rule for this kind of payment.",
  BANK_TYPE_NOT_IN_RULEBOOK:
    "The rules Recourse works with for unauthorised payments cover commercial banks only. Report it to your bank and on 1930 now.",
};

export function OwedPanel({ c }: { c: CaseView }) {
  const ent = c.entitlement;
  if (!ent) return null;

  const total = ent.money.reduce((n, m) => n + Number(m.amount_inr), 0);
  const accruing = ent.money.some((m) => m.accruing);
  const has = (code: string) => ent.flags.some((f) => f.code === code);
  const fraud = ent.case_type === "fraud";
  const reportBy = ent.deadlines.find((d) => d.key === "report_for_zero_liability" && !d.passed);
  const unverified = [...ent.citations, ...(c.escalation?.citations ?? [])].some((x) => !x.verified);
  const esc = c.escalation;
  const escDeadlines = esc?.deadlines ?? [];

  return (
    <Panel aria-labelledby="owed-title" className="flex flex-col gap-5">
      {fraud && (has("NOT_YET_REPORTED") || has("REPORT_TO_1930_NCRP")) ? (
        <Notice tone="warn" title="Report it now">
          Call 1930 or file at cybercrime.gov.in, and tell your bank.
          {reportBy ? ` Report by ${formatDate(reportBy.due)} to stay within the shortest window.` : ""}
        </Notice>
      ) : null}

      <div>
        <h2 id="owed-title" className="text-sm text-text-2">
          {ent.covered ? (total > 0 ? "What you're owed" : "Where you stand") : "What the rules say"}
        </h2>
        {ent.covered && total > 0 ? (
          <>
            <div className="figures mt-1 text-[2.4rem] leading-none font-[620] tracking-[-0.04em] text-blue-bright [font-stretch:88%]">
              {inr(total)}
            </div>
            <p className="mt-2 text-sm text-text-3">
              As of {formatDate(ent.as_of)}
              {accruing ? ". Grows by ₹100 every day the money stays unreturned." : "."}
            </p>
          </>
        ) : null}
        {!ent.covered ? (
          <p className="mt-2 text-[0.95rem] text-text-2">
            {NOT_COVERED[ent.not_covered_reason ?? ""] ?? "These rules don't cover this case."}
          </p>
        ) : null}
        {ent.covered && total === 0 && fraud ? (
          <p className="mt-2 text-[0.95rem] text-text-2">
            Who bears the loss depends on how it happened and how fast it was reported. The rules
            put the burden of proof on the bank.
          </p>
        ) : null}
      </div>

      {ent.money.length ? (
        <ul className="divide-y divide-line border-t border-line">
          {ent.money.map((m) => (
            <li key={m.key} className="flex flex-col gap-0.5 py-3">
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-[0.95rem] text-text">{m.label}</span>
                <span className="figures shrink-0 text-[0.95rem] text-text">{inr(m.amount_inr)}</span>
              </div>
              <Cite c={m.citation} />
            </li>
          ))}
        </ul>
      ) : null}

      {ent.deadlines.length || escDeadlines.length ? (
        <div>
          <h3 className="text-sm text-text-2">Dates that matter</h3>
          <ul className="mt-1 divide-y divide-line">
            {[...ent.deadlines, ...escDeadlines].map((d) => (
              <Deadline key={d.key} d={d} />
            ))}
          </ul>
        </div>
      ) : null}

      {unverified ? (
        <p className="text-sm leading-relaxed text-text-3">
          Each figure quotes the RBI document it links to. A final human check of these rules is
          still pending, so emails stay in the sandbox.
        </p>
      ) : null}
    </Panel>
  );
}
