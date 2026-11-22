"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { use, type ReactNode } from "react";

import { Notice, Working } from "@/components/app/ui";
import { CaseProvider } from "@/components/case/case-context";
import { StopCase } from "@/components/case/next-action";
import { StageTracker } from "@/components/case/stage-tracker";
import { api, ENDED, type CaseView } from "@/lib/api";
import { nextStep, STEP_TITLE } from "@/lib/case-progress";
import { formatDate, inr } from "@/lib/format";
import { usePoll } from "@/lib/hooks";
import { ENUMS } from "@/lib/labels";
import { cn } from "@/lib/utils";

const PAGES = [
  { slug: "", label: "Overview" },
  { slug: "/letters", label: "Letters and replies" },
  { slug: "/timeline", label: "Timeline" },
  { slug: "/evidence", label: "Evidence" },
];

function fact(c: CaseView, name: string): string | null {
  const v = c.evidence?.facts[name]?.value;
  return v === null || v === undefined || v === "" ? null : String(v);
}

function PageLinks({ c, vertical }: { c: CaseView; vertical: boolean }) {
  const path = usePathname();
  const base = `/cases/${c.id}`;
  const step = nextStep(c);
  return (
    <ul className={cn(vertical ? "flex flex-col gap-0.5" : "flex gap-1 overflow-x-auto pb-1")}>
      {PAGES.map((p) => {
        const href = base + p.slug;
        const on = path === href;
        return (
          <li key={p.slug} className="shrink-0">
            <Link
              href={href}
              aria-current={on ? "page" : undefined}
              className={cn(
                "flex items-center justify-between gap-3 rounded-full px-3.5 py-2 text-[0.93rem] whitespace-nowrap transition-colors",
                vertical && "rounded-[0.8rem]",
                on ? "bg-white/[0.07] text-text" : "text-text-2 hover:bg-white/[0.03] hover:text-text",
              )}
            >
              {p.label}
              {p.slug === "" && step.owner === "you" ? (
                <span aria-label="action needed" className="size-1.5 rounded-full bg-blue" />
              ) : null}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function CaseFacts({ c }: { c: CaseView }) {
  const amount = fact(c, "amount_inr");
  const channel = fact(c, "channel");
  const paid = fact(c, "txn_timestamp");
  const rows: [string, string | null][] = [
    ["Amount", amount ? inr(amount) : null],
    ["Paid by", channel ? (ENUMS.channel?.[channel] ?? channel) : null],
    ["Bank", c.bank?.name ?? fact(c, "payer_bank")],
    ["Payment date", paid ? formatDate(paid) : null],
    ["Bank's complaint no.", c.bank_complaint_ref],
    ["Ombudsman no.", c.ombudsman_ref],
  ];
  return (
    <dl className="flex flex-col gap-3 text-sm">
      {rows
        .filter(([, v]) => v)
        .map(([k, v]) => (
          <div key={k}>
            <dt className="text-text-3">{k}</dt>
            <dd className="figures mt-0.5 break-words text-text">{v}</dd>
          </div>
        ))}
    </dl>
  );
}

/** The frame of every case page: header, stage tracker, sidebar. Pages read the case via useCase(). */
export function CaseShell({ params, children }: { params: Promise<{ id: string }>; children: ReactNode }) {
  const { id } = use(params);
  const path = usePathname();
  const { data: c, error, settle: refresh } = usePoll(() => api.getCase(id), {
    active: (v) => !ENDED.includes(v.status),
    // What moves when the workflow applies an action: the status, a new audit event, the
    // flags it exposes (all part of the view), so compare the whole view minus the clock.
    changeKey: (v) => JSON.stringify({ ...v, last_thread_check: null }),
  });

  if (error && !c) {
    return (
      <div className="pt-10">
        <Notice tone="warn" title={error.status === 404 ? "This case isn't here." : "The case could not be loaded."}>
          {error.status === 404
            ? "It may belong to another browser, or the link is wrong. Cases stay with the browser that started them."
            : error.message}
        </Notice>
        <Link href="/cases" className="action-quiet mt-6">
          All cases
        </Link>
      </div>
    );
  }
  if (!c) {
    return (
      <div className="pt-12">
        <Working>Loading the case</Working>
      </div>
    );
  }

  const amount = fact(c, "amount_inr");
  const bank = c.bank?.name ?? fact(c, "payer_bank");
  const ended = ENDED.includes(c.status);
  const step = nextStep(c);
  const onOverview = path === `/cases/${c.id}`;

  return (
    <CaseProvider value={{ c, refresh }}>
      <div className="pt-4 sm:pt-6">
        <Link href="/cases" className="text-sm text-text-3 hover:text-text-2">
          All cases
        </Link>

        {/* Header ------------------------------------------------------------------------ */}
        <header className="mt-2 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
          <div className="min-w-0">
            <h1 className="text-[clamp(1.6rem,1.2rem+1.4vw,2.3rem)] leading-tight font-[620] tracking-[-0.03em] [font-stretch:90%]">
              {bank ?? "Your case"}
              {amount ? <span className="figures text-text-2">, {inr(amount)}</span> : null}
            </h1>
            <p className="mt-1 text-sm text-text-3">
              Case <span className="figures text-text-2">{c.case_tag}</span>, started {formatDate(c.created_at)}
            </p>
          </div>
        </header>

        <div className="mt-5">
          <StageTracker c={c} />
        </div>

        {error ? (
          <p className="mt-3 text-sm text-text-3" role="status">
            Couldn&apos;t refresh just now; retrying.
          </p>
        ) : null}

        {/* Sidebar + page -------------------------------------------------------------- */}
        <div className="mt-6 grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-8">
          <aside className="min-w-0 lg:sticky lg:top-6 lg:self-start">
            <nav aria-label="Case pages" className="lg:hidden">
              <PageLinks c={c} vertical={false} />
            </nav>
            <div className="hidden flex-col gap-6 lg:flex">
              <nav aria-label="Case pages">
                <PageLinks c={c} vertical />
              </nav>
              {!ended && !onOverview ? (
                <Link
                  href={`/cases/${c.id}`}
                  className={cn(
                    "rounded-[1rem] border px-3.5 py-3 text-sm transition-colors",
                    step.owner === "you" ? "border-blue/40 bg-blue/[0.07] hover:bg-blue/[0.11]" : "border-line hover:bg-white/[0.03]",
                  )}
                >
                  <span className="block text-text-3">{step.owner === "you" ? "Your next step" : "Right now"}</span>
                  <span className="mt-0.5 block text-text">{STEP_TITLE[step.key]}</span>
                </Link>
              ) : null}
              <div className="border-t border-line pt-5">
                <CaseFacts c={c} />
              </div>
              {!ended && c.status !== "NEW" ? (
                <div className="border-t border-line pt-4">
                  <StopCase c={c} refresh={refresh} />
                </div>
              ) : null}
            </div>
          </aside>

          <div className="min-w-0">{children}</div>
        </div>

        {!ended && c.status !== "NEW" ? (
          <div className="mt-10 border-t border-line pt-4 lg:hidden">
            <StopCase c={c} refresh={refresh} />
          </div>
        ) : null}
      </div>
    </CaseProvider>
  );
}
