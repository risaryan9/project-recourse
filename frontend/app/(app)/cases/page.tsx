"use client";

import Link from "next/link";

import { Notice, StatusPill } from "@/components/app/ui";
import { api, ENDED, type CaseSummary } from "@/lib/api";
import { daysFromToday, formatDate, inr } from "@/lib/format";
import { usePoll } from "@/lib/hooks";

function Countdown({ due }: { due: string }) {
  const days = daysFromToday(due);
  if (days === 0) return <>today</>;
  if (days === 1) return <>tomorrow</>;
  return <>in {days} days</>;
}

function CaseRow({ c }: { c: CaseSummary }) {
  const ended = ENDED.includes(c.status);
  return (
    <Link
      href={`/cases/${c.id}`}
      className="glass group grid gap-4 rounded-[1.5rem] p-5 transition-colors hover:border-line-strong sm:grid-cols-[1fr_auto] sm:items-center sm:gap-8 sm:p-6"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-[1.05rem] font-[560] text-text">
            {c.bank_name ?? "Your bank"}
            {c.amount_inr ? (
              <span className="figures font-normal text-text-2">, {inr(c.amount_inr)}</span>
            ) : null}
          </span>
          <StatusPill status={c.status} />
        </div>
        <div className="mt-2 text-sm text-text-3">
          <span className="figures">{c.case_tag}</span>, started {formatDate(c.created_at)}
          {c.next_deadline && !ended ? (
            <>
              {". "}
              <span className="text-text-2">
                {c.next_deadline.label}: {formatDate(c.next_deadline.due)} (
                <Countdown due={c.next_deadline.due} />)
              </span>
            </>
          ) : null}
        </div>
      </div>
      {!ended && c.total_owed_inr && Number(c.total_owed_inr) > 0 ? (
        <div className="sm:text-right">
          <div className="text-sm text-text-3">Owed to you</div>
          <div className="figures text-2xl font-[600] tracking-[-0.03em] text-blue-bright [font-stretch:90%]">
            {inr(c.total_owed_inr)}
          </div>
        </div>
      ) : null}
    </Link>
  );
}

export default function CasesPage() {
  const { data, error, loading } = usePoll(api.listCases, { intervalMs: 8000 });

  return (
    <div className="pt-8 sm:pt-12">
      <div className="flex flex-wrap items-end justify-between gap-6">
        <div>
          <h1 className="type-title">Your cases</h1>
          <p className="mt-2 text-text-2">Each case runs on its own. Open one to see what to do next.</p>
        </div>
        <Link href="/cases/new" className="action-primary h-11 px-5 text-[0.95rem]">
          Start a case
        </Link>
      </div>

      <div className="mt-10 flex flex-col gap-4">
        {error ? <Notice tone="warn" title="Your cases could not be loaded.">{error.message}</Notice> : null}
        {loading && !data ? <p className="text-text-3">Loading your cases…</p> : null}
        {data && data.length === 0 ? (
          <div className="glass rounded-[1.5rem] p-8 text-center sm:p-12">
            <p className="type-heading">No cases yet.</p>
            <p className="mx-auto mt-2 max-w-[44ch] text-text-2">
              Start one with the bank&apos;s SMS, a screenshot or a few words about what happened.
            </p>
            <Link href="/cases/new" className="action-primary mt-6 h-11 px-5 text-[0.95rem]">
              Start a case
            </Link>
          </div>
        ) : null}
        {data?.map((c) => <CaseRow key={c.id} c={c} />)}
      </div>
    </div>
  );
}
