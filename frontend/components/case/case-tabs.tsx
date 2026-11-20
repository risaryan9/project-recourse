"use client";

import { useEffect, useState } from "react";

import { Panel } from "@/components/app/ui";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, type CaseView, type Reply } from "@/lib/api";
import { formatDateTime, inr } from "@/lib/format";
import { describeEvent, ENUMS, FACTS } from "@/lib/labels";
import { cn } from "@/lib/utils";

const ACTOR: Record<string, string> = { USER: "You", SYSTEM: "Recourse", AGENT: "Recourse", BANK: "Bank" };

function Timeline({ c }: { c: CaseView }) {
  const events = [...c.timeline].reverse();
  return (
    <ol className="divide-y divide-line">
      {events.map((e, i) => (
        <li key={i} className="grid grid-cols-[6.5rem_1fr] gap-4 py-3 sm:grid-cols-[8rem_1fr]">
          <time className="figures text-sm text-text-3" dateTime={e.occurred_at}>
            {formatDateTime(e.occurred_at)}
          </time>
          <div className="min-w-0">
            <p className={cn("text-[0.95rem]", e.event_type === "reply.suspicious" ? "text-amber" : "text-text")}>
              {describeEvent(e)}
            </p>
            <p className="text-sm text-text-3">{ACTOR[e.actor] ?? e.actor}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

const REPLY_LABEL: Record<string, string> = {
  ACKNOWLEDGEMENT: "Acknowledgement",
  RESOLVED: "Says it paid",
  PARTIAL: "Paid part",
  REJECTED_WITH_REASONS: "Rejected, with reasons",
  REJECTED_NO_REASONS: "Rejected, no reasons",
  NEEDS_INFO: "Asks for information",
  UNCLEAR: "Unclear",
};

function Emails({ c }: { c: CaseView }) {
  const [replies, setReplies] = useState<Reply[] | null>(null);
  const count = c.timeline.filter((e) => e.event_type.startsWith("reply.")).length;
  useEffect(() => {
    api.replies(c.id).then(setReplies, () => setReplies([]));
  }, [c.id, count]);
  const e = c.email;
  return (
    <div className="flex flex-col gap-6">
      {e ? (
        <article className="rounded-2xl border border-line p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h3 className="font-[560] text-text">Your letter to the bank</h3>
            <span className="text-sm text-text-3">
              {e.status === "SENT" ? `Sent ${formatDateTime(e.sent_at)}` : e.status === "PREPARED" ? "Ready to send" : "Not ready"}
              {e.sandbox ? ", sandbox" : ""}
            </span>
          </div>
          <p className="mt-1 text-sm text-text-3">To {e.to ?? "not set"}</p>
          <p className="mt-3 text-sm font-[560] text-text-2">{e.subject}</p>
          <p className="mt-2 max-h-64 overflow-y-auto text-sm leading-relaxed whitespace-pre-wrap text-text-2">{e.body}</p>
        </article>
      ) : (
        <p className="text-text-3">No email yet.</p>
      )}
      {replies?.map((r) => (
        <article key={r.id} className={cn("rounded-2xl border p-4", r.suspicious ? "border-amber/30" : "border-line")}>
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h3 className="font-[560] text-text">
              Reply from {r.sender ?? "the bank"}
            </h3>
            <span className="text-sm text-text-3">{formatDateTime(r.received_at)}</span>
          </div>
          <p className="mt-1 text-sm text-text-3">
            {r.status === "PENDING"
              ? "Being checked"
              : r.suspicious
                ? "Flagged as a possible scam"
                : (REPLY_LABEL[r.label ?? ""] ?? r.label)}
          </p>
          <p className="mt-3 max-h-64 overflow-y-auto text-sm leading-relaxed whitespace-pre-wrap text-text-2">{r.text}</p>
        </article>
      ))}
    </div>
  );
}

function Evidence({ c }: { c: CaseView }) {
  const ev = c.evidence;
  if (!ev) return <p className="text-text-3">Nothing read yet.</p>;
  const rows = FACTS.filter((f) => ev.facts[f.name]?.value !== null && ev.facts[f.name]?.value !== undefined);
  return (
    <div className="flex flex-col gap-4">
      <dl className="divide-y divide-line">
        {rows.map((f) => {
          const fact = ev.facts[f.name];
          const v = fact.value;
          const shown =
            f.kind === "money"
              ? inr(String(v))
              : f.kind === "datetime"
                ? f.name === "txn_timestamp" && !ev.txn_time_known
                  ? `${formatDateTime(String(v)).split(",")[0]} (time not shown)`
                  : formatDateTime(String(v))
                : f.kind === "bool"
                  ? v
                    ? "Yes"
                    : "No"
                  : f.kind === "enum"
                    ? (ENUMS[f.name]?.[String(v)] ?? String(v))
                    : String(v);
          return (
            <div key={f.name} className="grid gap-1 py-3 sm:grid-cols-[14rem_1fr] sm:gap-6">
              <dt className="text-sm text-text-3">{f.label}</dt>
              <dd>
                <span className="text-[0.95rem] text-text">{shown}</span>
                <span className="block text-sm text-text-3">
                  {fact.origin === "USER" ? "Confirmed by you" : fact.source_text ? `From: "${fact.source_text}"` : "Read from your evidence"}
                </span>
              </dd>
            </div>
          );
        })}
      </dl>
      {ev.redactions.length ? (
        <p className="text-sm text-text-3">Sensitive codes or numbers were removed before storing.</p>
      ) : null}
    </div>
  );
}

export function CaseTabs({ c }: { c: CaseView }) {
  return (
    <Panel>
      <Tabs defaultValue="timeline">
        <TabsList className="mb-4">
          <TabsTrigger value="timeline">Timeline</TabsTrigger>
          <TabsTrigger value="emails">Emails and replies</TabsTrigger>
          <TabsTrigger value="evidence">Evidence</TabsTrigger>
        </TabsList>
        <TabsContent value="timeline">
          <Timeline c={c} />
        </TabsContent>
        <TabsContent value="emails">
          <Emails c={c} />
        </TabsContent>
        <TabsContent value="evidence">
          <Evidence c={c} />
        </TabsContent>
      </Tabs>
    </Panel>
  );
}
