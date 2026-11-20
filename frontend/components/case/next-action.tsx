"use client";

import { useEffect, useState } from "react";

import {
  CopyButton,
  fieldClass,
  FormError,
  Notice,
  Panel,
  PrimaryButton,
  QuietButton,
  TextButton,
  Working,
} from "@/components/app/ui";
import { LetterReview } from "@/components/case/letter-review";
import { ReviewFacts } from "@/components/case/review-facts";
import { api, type Bank, type CaseView, type Filing, type ReplySummary } from "@/lib/api";
import { daysFromToday, formatDate, inr } from "@/lib/format";
import { ENUMS } from "@/lib/labels";

type Props = { c: CaseView; refresh: () => void };

/** Run an API call with busy/error state; refresh the case afterwards. */
function useAction(refresh: () => void) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try {
      await fn();
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  return { busy, error, run };
}

function Heading({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div>
      <h2 className="type-heading">{title}</h2>
      {children ? <p className="mt-1.5 text-text-2">{children}</p> : null}
    </div>
  );
}

/* ----------------------------------------------------------------------------------------- */
/* Sending the letter yourself (ManualChannel)                                               */
/* ----------------------------------------------------------------------------------------- */

function SendYourself({ c, refresh }: Props) {
  const e = c.email!;
  const { busy, error, run } = useAction(refresh);
  return (
    <div className="flex flex-col gap-6">
      <Heading title="Send the letter from your email">
        Recourse never sends on its own. Copy the letter into your email, send it, then tell Recourse.
      </Heading>
      {e.sandbox ? (
        <Notice title="Sandbox: not sent to the real bank">
          This address is a test inbox. Real sending stays off until the bank&apos;s address and the
          rules are verified.
        </Notice>
      ) : null}
      <dl className="flex flex-col gap-4">
        {[
          ["To", e.to ?? ""],
          ["Subject", e.subject],
        ].map(([k, v]) => (
          <div key={k} className="grid grid-cols-[1fr_auto] items-center gap-3">
            <div className="min-w-0">
              <dt className="text-sm text-text-3">{k}</dt>
              <dd className="break-words text-text">{v}</dd>
            </div>
            <CopyButton text={v} />
          </div>
        ))}
        <div>
          <div className="flex items-center justify-between gap-3">
            <dt className="text-sm text-text-3">Letter</dt>
            <CopyButton text={e.body} />
          </div>
          <dd className="mt-2 max-h-72 overflow-y-auto rounded-xl border border-line bg-white/[0.02] p-4 text-sm leading-relaxed whitespace-pre-wrap text-text-2">
            {e.body}
          </dd>
        </div>
      </dl>
      <FormError message={error ?? c.last_error} />
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <PrimaryButton busy={busy === "sent"} onClick={() => run("sent", () => api.markSent(c.id))}>
          I&apos;ve sent it
        </PrimaryButton>
        {e.mailto ? (
          <a href={e.mailto} className="text-[0.95rem] text-text-2 underline-offset-4 hover:text-text hover:underline">
            Open in my email app
          </a>
        ) : null}
      </div>
    </div>
  );
}

function BlockedSend({ c, refresh }: Props) {
  const { busy, error, run } = useAction(refresh);
  const [banks, setBanks] = useState<Bank[]>([]);
  const [bankId, setBankId] = useState(c.bank?.id ?? "");
  useEffect(() => {
    api.banks().then(setBanks, () => setBanks([]));
  }, []);
  return (
    <div className="flex flex-col gap-6">
      <Heading title="The email can't be prepared yet">{c.email?.blocked_reasons.join(" ")}</Heading>
      <div>
        <label htmlFor="bank" className="text-sm text-text-2">
          Your bank
        </label>
        <select id="bank" value={bankId} onChange={(e) => setBankId(e.target.value)} className={`${fieldClass} mt-1.5 max-w-sm`}>
          <option value="">Choose your bank…</option>
          {banks.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
      </div>
      <FormError message={error} />
      <div className="flex flex-wrap gap-3">
        <PrimaryButton
          busy={busy === "retry"}
          onClick={() =>
            run("retry", async () => {
              if (bankId && bankId !== c.bank?.id) await api.chooseBank(c.id, bankId);
              await api.retrySend(c.id);
            })
          }
        >
          Try again
        </PrimaryButton>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------------------------------- */
/* Waiting for the bank                                                                      */
/* ----------------------------------------------------------------------------------------- */

const VERDICT: Record<ReplySummary["next_action"], string> = {
  KEEP_WAITING: "The bank acknowledged your complaint. That isn't a decision, so the deadline still stands.",
  USER_ACTION: "The bank asked you for more information. Reply to the bank directly; the deadline still stands.",
  PROPOSE_RESOLVED: "The bank says it has paid what you're owed. Check your account, then confirm below.",
  ASK_USER: "The bank turned your complaint down and gave reasons, or its reply was unclear. You decide what happens next.",
  ESCALATE: "The bank's reply doesn't give you what the rules say you're owed.",
  WARN: "",
};

function LastReply({ r }: { r: ReplySummary }) {
  if (r.suspicious) {
    return (
      <Notice tone="warn" title="Be careful with the last reply">
        <ul className="list-disc space-y-1 pl-5">
          {r.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
        <p className="mt-2">Recourse took no action on it. Your case is unchanged.</p>
      </Notice>
    );
  }
  return <Notice title="Latest reply from the bank">{VERDICT[r.next_action]}</Notice>;
}

function ReplyForm({ c, refresh }: Props) {
  const { busy, error, run } = useAction(refresh);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [sender, setSender] = useState("");
  if (!open) return <QuietButton onClick={() => setOpen(true)}>Paste the bank&apos;s reply</QuietButton>;
  return (
    <div className="flex flex-col gap-3">
      <label htmlFor="reply" className="text-sm text-text-2">
        The bank&apos;s reply, exactly as you received it
      </label>
      <textarea
        id="reply"
        rows={6}
        value={text}
        onChange={(e) => setText(e.target.value)}
        className="w-full rounded-xl border border-line-strong bg-white/[0.03] px-3.5 py-3 text-[0.95rem] text-text outline-none focus-visible:border-blue"
      />
      <label htmlFor="sender" className="text-sm text-text-2">
        Sender&apos;s email address (optional; helps spot fakes)
      </label>
      <input id="sender" type="email" value={sender} onChange={(e) => setSender(e.target.value)} className={`${fieldClass} max-w-sm`} />
      <FormError message={error} />
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <PrimaryButton
          busy={busy === "reply"}
          disabled={!text.trim()}
          onClick={() =>
            run("reply", async () => {
              await api.addReply(c.id, { text, sender: sender.trim() || undefined });
              setText("");
              setOpen(false);
            })
          }
        >
          Check this reply
        </PrimaryButton>
        <TextButton onClick={() => setOpen(false)}>Cancel</TextButton>
      </div>
    </div>
  );
}

function ResolvedForm({ c, refresh, label = "I got my money" }: Props & { label?: string }) {
  const { busy, error, run } = useAction(refresh);
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState("");
  if (!open) return <TextButton onClick={() => setOpen(true)}>{label}</TextButton>;
  return (
    <div className="flex flex-col gap-3">
      <label htmlFor="amount" className="text-sm text-text-2">
        How much did you receive in total, in rupees?
      </label>
      <input
        id="amount"
        inputMode="decimal"
        value={amount}
        onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))}
        className={`${fieldClass} max-w-[12rem]`}
      />
      <FormError message={error} />
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <QuietButton busy={busy === "resolved"} disabled={!amount} onClick={() => run("resolved", () => api.resolved(c.id, amount))}>
          Close the case as resolved
        </QuietButton>
        <TextButton onClick={() => setOpen(false)}>Cancel</TextButton>
      </div>
    </div>
  );
}

function WaitingForBank({ c, refresh }: Props) {
  const esc = c.escalation;
  const until = esc?.waiting_period_ends;
  const days = until ? daysFromToday(until) : null;
  return (
    <div className="flex flex-col gap-6">
      <Heading title="Waiting for your bank">
        {until
          ? `Your bank has until ${formatDate(until, { long: true })} to put it right${
              days !== null && days >= 0 ? `, ${days === 0 ? "today" : `${days} days from now`}` : ""
            }. When it replies, paste the reply here and Recourse checks it against the rules.`
          : "When your bank replies, paste the reply here and Recourse checks it against the rules."}
      </Heading>
      {c.bank_complaint_ref ? (
        <p className="text-sm text-text-3">
          Bank&apos;s complaint number: <span className="text-text-2">{c.bank_complaint_ref}</span>
        </p>
      ) : null}
      {c.last_reply ? <LastReply r={c.last_reply} /> : null}
      <FormError message={c.last_error} />
      <div className="flex flex-col items-start gap-4">
        <ReplyForm c={c} refresh={refresh} />
        <ResolvedForm c={c} refresh={refresh} />
      </div>
    </div>
  );
}

function Decision({ c, refresh }: Props) {
  const { busy, error, run } = useAction(refresh);
  const r = c.last_reply;
  const paid = r?.next_action === "PROPOSE_RESOLVED";
  return (
    <div className="flex flex-col gap-6">
      <Heading title={paid ? "The bank says it has paid" : "The bank's reply needs your decision"}>
        {r ? VERDICT[r.next_action] : null}
      </Heading>
      {paid ? (
        <ResolvedForm c={c} refresh={refresh} label="Yes, I received it" />
      ) : (
        <p className="text-text-2">
          If you think the reply is wrong, you can take the complaint to the RBI Ombudsman. Recourse
          drafts it for you.
        </p>
      )}
      <FormError message={error ?? c.last_error} />
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <PrimaryButton busy={busy === "esc"} onClick={() => run("esc", () => api.escalate(c.id))}>
          {paid ? "The money hasn't arrived: go to the Ombudsman" : "Go to the RBI Ombudsman"}
        </PrimaryButton>
        {!paid ? (
          <>
            <ResolvedForm c={c} refresh={refresh} />
            <TextButton onClick={() => run("stop", () => api.abandon(c.id, "accepted_bank_reply"))}>
              Accept the bank&apos;s answer and stop
            </TextButton>
          </>
        ) : null}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------------------------------- */
/* Escalation to the RBI Ombudsman                                                           */
/* ----------------------------------------------------------------------------------------- */

function BankTypeForm({ c, refresh }: Props) {
  const { busy, error, run } = useAction(refresh);
  const [value, setValue] = useState("");
  return (
    <div className="flex flex-col gap-6">
      <Heading title="One question before the Ombudsman">
        The RBI Ombudsman covers most banks, but not every kind. What kind of bank is it?
      </Heading>
      <select value={value} onChange={(e) => setValue(e.target.value)} aria-label="Kind of bank" className={`${fieldClass} max-w-md`}>
        <option value="">Choose…</option>
        {Object.entries(ENUMS.bank_type).map(([k, label]) => (
          <option key={k} value={k}>
            {label}
          </option>
        ))}
      </select>
      <FormError message={error} />
      <div>
        <PrimaryButton busy={busy === "type"} disabled={!value} onClick={() => run("type", () => api.setBankType(c.id, value))}>
          Continue
        </PrimaryButton>
      </div>
    </div>
  );
}

function FilingPackage({ c, refresh }: Props) {
  const { busy, error, run } = useAction(refresh);
  const [filing, setFiling] = useState<Filing | null>(null);
  const [ref, setRef] = useState("");
  useEffect(() => {
    api.filing(c.id).then(setFiling, () => setFiling(null));
  }, [c.id]);

  return (
    <div className="flex flex-col gap-6">
      <Heading title="File your complaint with the RBI Ombudsman">
        The RBI&apos;s portal needs you to file it yourself. Everything below is ready to copy into its form.
      </Heading>
      {filing?.filing_deadline ? (
        <Notice tone={c.filing_deadline_passed ? "warn" : "info"} title={`Last day to file: ${formatDate(filing.filing_deadline, { long: true })}`}>
          {c.filing_deadline_passed
            ? "This date has passed. The Ombudsman may not accept the complaint now."
            : `${daysFromToday(filing.filing_deadline)} days left.`}
        </Notice>
      ) : null}
      {filing ? (
        <>
          <dl className="divide-y divide-line border-y border-line">
            {filing.fields.map((f) => (
              <div key={f.label} className="grid grid-cols-[1fr_auto] items-center gap-3 py-3">
                <div className="min-w-0">
                  <dt className="text-sm text-text-3">{f.label}</dt>
                  <dd className="text-text">{f.value}</dd>
                </div>
                <CopyButton text={f.value} />
              </div>
            ))}
          </dl>
          <div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm text-text-3">Your complaint</span>
              <CopyButton text={`${filing.title}\n\n${filing.complaint_text}`} />
            </div>
            <p className="mt-2 max-h-72 overflow-y-auto rounded-xl border border-line bg-white/[0.02] p-4 text-sm leading-relaxed whitespace-pre-wrap text-text-2">
              {filing.complaint_text}
            </p>
          </div>
          <div className="text-sm text-text-3">
            <p>These labels describe what each field asks for; the portal&apos;s own wording may differ.</p>
            <p className="mt-2">You can also file another way:</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">
              {filing.other_ways.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </div>
          <a
            href={filing.portal_url}
            target="_blank"
            rel="noreferrer"
            className="action-quiet w-fit"
          >
            Open cms.rbi.org.in
          </a>
        </>
      ) : (
        <Working>Preparing the filing details</Working>
      )}
      <div className="flex flex-col gap-3 border-t border-line pt-6">
        <label htmlFor="cmsref" className="text-sm text-text-2">
          Complaint number from the portal (optional)
        </label>
        <input id="cmsref" value={ref} onChange={(e) => setRef(e.target.value)} className={`${fieldClass} max-w-xs`} />
        <FormError message={error ?? c.last_error} />
        <div>
          <PrimaryButton busy={busy === "filed"} onClick={() => run("filed", () => api.markFiled(c.id, ref.trim() || undefined))}>
            I&apos;ve filed it
          </PrimaryButton>
        </div>
      </div>
    </div>
  );
}

function CloseForm({ c, refresh }: Props) {
  const { busy, error, run } = useAction(refresh);
  const [outcome, setOutcome] = useState("");
  const [amount, setAmount] = useState("");
  return (
    <div className="flex flex-col gap-6">
      <Heading title="Filed with the RBI Ombudsman">
        {c.ombudsman_filed_at ? `You filed it on ${formatDate(c.ombudsman_filed_at, { long: true })}` : "You filed it"}
        {c.ombudsman_ref ? ` (complaint ${c.ombudsman_ref})` : ""}. When you hear back, record how it ended.
      </Heading>
      <select value={outcome} onChange={(e) => setOutcome(e.target.value)} aria-label="How it ended" className={`${fieldClass} max-w-md`}>
        <option value="">How did it end?</option>
        {Object.entries(ENUMS.outcome).map(([k, label]) => (
          <option key={k} value={k}>
            {label}
          </option>
        ))}
      </select>
      {outcome === "AWARD_RECEIVED" || outcome === "SETTLED_BY_BANK" ? (
        <input
          inputMode="decimal"
          placeholder="Amount received, in rupees"
          aria-label="Amount received, in rupees"
          value={amount}
          onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))}
          className={`${fieldClass} max-w-[16rem]`}
        />
      ) : null}
      <FormError message={error} />
      <div>
        <PrimaryButton busy={busy === "close"} disabled={!outcome} onClick={() => run("close", () => api.close(c.id, outcome, amount))}>
          Close the case
        </PrimaryButton>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------------------------------- */
/* The panel                                                                                 */
/* ----------------------------------------------------------------------------------------- */

function Ended({ c }: { c: CaseView }) {
  const last = [...c.timeline].reverse().find((e) => ["case.resolved", "case.closed", "case.abandoned"].includes(e.event_type));
  const d = (last?.details ?? {}) as Record<string, string | null>;
  if (c.status === "RESOLVED") {
    return (
      <Heading title="Resolved">
        {d.amount_received_inr ? `You received ${inr(d.amount_received_inr)}. ` : ""}The case is closed.
      </Heading>
    );
  }
  if (c.status === "CLOSED") {
    return <Heading title="Closed">{ENUMS.outcome[d.outcome ?? ""] ?? "The case is closed."}</Heading>;
  }
  return (
    <Heading title="Stopped">
      {d.reason === "not_payment_evidence"
        ? "The upload didn't look like evidence of a payment. Start a new case with the bank's SMS or a screenshot."
        : "This case was stopped. Nothing more will happen."}
    </Heading>
  );
}

function Stop({ c, refresh }: Props) {
  const { error, run } = useAction(refresh);
  const [sure, setSure] = useState(false);
  return (
    <div className="flex flex-col gap-2">
      {sure ? (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
          <span className="text-text-2">Stop this case? It can&apos;t be restarted.</span>
          <TextButton className="text-sm text-destructive" onClick={() => run("stop", () => api.abandon(c.id, "user_abandoned"))}>
            Stop the case
          </TextButton>
          <TextButton className="text-sm" onClick={() => setSure(false)}>
            Keep it
          </TextButton>
        </div>
      ) : (
        <TextButton className="w-fit text-sm text-text-3" onClick={() => setSure(true)}>
          Stop this case
        </TextButton>
      )}
      <FormError message={error} />
    </div>
  );
}

function Body({ c, refresh }: Props) {
  const draft = c.draft;
  if (c.status === "RESOLVED" || c.status === "CLOSED" || c.status === "ABANDONED") return <Ended c={c} />;
  if (c.status === "NEW" || (c.status === "EXTRACTED" && !c.evidence)) return <Working>Reading your evidence. This takes a few seconds.</Working>;
  if (c.status === "EXTRACTED") return <ReviewFacts c={c} onDone={refresh} />;
  if (c.needs_bank_type) return <BankTypeForm c={c} refresh={refresh} />;
  if (c.can_recheck_escalation) {
    return (
      <div className="flex flex-col gap-5">
        <Heading title="The Ombudsman complaint can't be prepared yet">{c.last_error}</Heading>
        <div>
          <QuietButton onClick={async () => { await api.recheckEscalation(c.id); refresh(); }}>Check again</QuietButton>
        </div>
      </div>
    );
  }
  if (c.can_redraft) {
    return (
      <div className="flex flex-col gap-5">
        <Heading title="The letter didn't pass its checks">
          Recourse only shows letters whose every amount and date matches the rules. This one
          didn&apos;t, so it was held back. Try once more.
        </Heading>
        <div>
          <PrimaryButton onClick={async () => { await api.redraft(c.id); refresh(); }}>Write it again</PrimaryButton>
        </div>
      </div>
    );
  }
  if (draft?.status === "PENDING" && (c.status === "AWAITING_APPROVAL" || c.status === "ESCALATION_DRAFT_READY")) {
    return <LetterReview key={draft.id} c={c} draft={draft} onDone={refresh} />;
  }
  if (c.can_retry_send && c.email?.status === "BLOCKED") return <BlockedSend c={c} refresh={refresh} />;
  if (c.email?.status === "PREPARED" && c.status === "AWAITING_APPROVAL") return <SendYourself c={c} refresh={refresh} />;
  if (c.awaiting_filing) return <FilingPackage c={c} refresh={refresh} />;
  if (c.can_close) return <CloseForm c={c} refresh={refresh} />;
  if (c.status === "JUDGED" && c.can_escalate) return <Decision c={c} refresh={refresh} />;
  if (c.status === "JUDGED" || c.bank_wait_expired || c.status === "ESCALATION_DRAFT_READY") {
    return <Working>Preparing your complaint to the RBI Ombudsman. Every figure is checked first.</Working>;
  }
  if (c.status === "AWAITING_BANK") return <WaitingForBank c={c} refresh={refresh} />;
  if (c.status === "ASSESSED" && c.entitlement && !c.entitlement.covered) {
    return (
      <Heading title="These rules don't cover this case">
        See what the rules say on the right. You can still contact your bank directly.
      </Heading>
    );
  }
  if (c.status === "ASSESSED") return <Working>Writing your letter and checking every figure in it.</Working>;
  if (c.status === "SENT_TO_BANK") return <Working>Starting the bank&apos;s reply window.</Working>;
  return <Working>Working on your case.</Working>;
}

export function NextAction({ c, refresh }: Props) {
  const ended = c.status === "RESOLVED" || c.status === "CLOSED" || c.status === "ABANDONED";
  return (
    <Panel lit aria-label="Next action" className="flex flex-col gap-8">
      <Body c={c} refresh={refresh} />
      {!ended && c.status !== "NEW" ? <Stop c={c} refresh={refresh} /> : null}
    </Panel>
  );
}
