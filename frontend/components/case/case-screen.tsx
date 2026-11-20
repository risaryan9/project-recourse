"use client";

import Link from "next/link";
import { use } from "react";

import { Notice, StatusPill, Working } from "@/components/app/ui";
import { CaseTabs } from "@/components/case/case-tabs";
import { NextAction } from "@/components/case/next-action";
import { OwedPanel } from "@/components/case/owed-panel";
import { api, ENDED } from "@/lib/api";
import { formatDate, inr } from "@/lib/format";
import { usePoll } from "@/lib/hooks";

export function CaseScreen({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: c, error, refresh } = usePoll(() => api.getCase(id), {
    active: (v) => !ENDED.includes(v.status),
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
  if (!c) return <div className="pt-12"><Working>Loading the case</Working></div>;

  const amount = c.evidence?.facts.amount_inr?.value as string | undefined;
  const bank = c.bank?.name ?? (c.evidence?.facts.payer_bank?.value as string | undefined);

  return (
    <div className="pt-6 sm:pt-10">
      <Link href="/cases" className="text-sm text-text-3 hover:text-text-2">
        All cases
      </Link>
      <header className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="type-title">
            {bank ?? "Your case"}
            {amount ? <span className="figures text-text-2">, {inr(amount)}</span> : null}
          </h1>
          <p className="mt-2 text-sm text-text-3">
            Case <span className="figures">{c.case_tag}</span>, started {formatDate(c.created_at)}
          </p>
        </div>
        <StatusPill status={c.status} />
      </header>

      {error ? (
        <p className="mt-4 text-sm text-text-3" role="status">
          Couldn&apos;t refresh just now; retrying.
        </p>
      ) : null}

      <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_22rem] lg:items-start">
        <NextAction c={c} refresh={refresh} />
        <div className="lg:sticky lg:top-6">
          <OwedPanel c={c} />
        </div>
        <div className="lg:col-span-1">
          <CaseTabs c={c} />
        </div>
      </div>
    </div>
  );
}
