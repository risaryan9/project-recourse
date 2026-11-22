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
import { GmailLogo } from "@/components/brand/gmail-logo";
import { LetterReview } from "@/components/case/letter-review";
import { ReviewFacts } from "@/components/case/review-facts";
import { api, type Bank, type CaseView, type Filing, type ReplySummary } from "@/lib/api";
import { currentStage, nextStep, OWNER_LABEL, STAGES, type Owner, type StepKey } from "@/lib/case-progress";
import { daysFromToday, formatDate, formatDateTime, inr } from "@/lib/format";
import { ENUMS } from "@/lib/labels";
import { cn } from "@/lib/utils";

type Props = { c: CaseView; refresh: () => void | Promise<void> };

/** Run an API call with busy/error state; refresh the case afterwards. */
function useAction(refresh: () => void | Promise<void>) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try {
      await fn();
      // Stay busy until the case has actually moved (see usePoll's settle), so the form
      // doesn't reappear for a moment as if nothing happened.
      await refresh();
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

function GmailRetry({ c, refresh }: Props) {
  const { busy, error, run } = useAction(refresh);
  const unsure = c.email?.status === "SENDING";
  return (
    <div className="flex flex-col gap-6">
      <Heading title={unsure ? "Check your Gmail before sending again" : "Gmail didn't send the letter"}>
        {c.last_error}
      </Heading>
      {unsure ? (
        <Notice>
          Open your Gmail <span className="text-text">Sent</span> folder. If the letter is there,
          don&apos;t send it again: paste the bank&apos;s reply here when it comes. If it isn&apos;t
          there, send it again.
        </Notice>
      ) : null}
      <FormError message={error} />
      <div>
        <PrimaryButton busy={busy === "retry"} onClick={() => run("retry", () => api.retrySend(c.id))}>
          Send it again
        </PrimaryButton>
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

function ReplyForm({ c, refresh, fallback = false }: Props & { fallback?: boolean }) {
  const { busy, error, run } = useAction(refresh);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [sender, setSender] = useState("");
  if (!open) {
    return fallback ? (
      <p className="text-sm text-text-3">
        Bank replied in a different email, or by letter?{" "}
        <TextButton className="text-sm text-text-2 underline underline-offset-4 hover:text-text" onClick={() => setOpen(true)}>
          Paste its reply instead
        </TextButton>
      </p>
    ) : (
      <QuietButton onClick={() => setOpen(true)}>Paste the bank&apos;s reply</QuietButton>
    );
  }
  return (
    <div className="flex w-full flex-col gap-3">
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

/** "2 min ago", kept fresh while the panel is open. */
function useAgo(iso: string | null): string | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(t);
  }, []);
  if (!iso) return null;
  const secs = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (secs < 10) return "just now";
  if (secs < 60) return `${secs} seconds ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} ${mins === 1 ? "minute" : "minutes"} ago`;
  return formatDateTime(iso);
}

/** Recourse is watching the email's Gmail thread: a quiet, living sign that it is looking. */
function MailWatch({ c }: { c: CaseView }) {
  const ago = useAgo(c.last_thread_check);
  return (
    <div role="status" className="flex items-center gap-4 rounded-[1rem] border border-line bg-white/[0.02] px-4 py-3.5">
      <span aria-hidden="true" className="relative grid size-10 shrink-0 place-items-center">
        <span className="absolute inset-0 rounded-full border border-blue/40 motion-safe:animate-[mail-watch_2.8s_var(--ease-calm)_infinite]" />
        <span className="absolute inset-0 rounded-full border border-blue/30 motion-safe:animate-[mail-watch_2.8s_var(--ease-calm)_1.4s_infinite]" />
        <span className="relative grid size-8 place-items-center rounded-full bg-white/[0.06]">
          <GmailLogo className="h-3 w-auto" />
        </span>
      </span>
      <div className="min-w-0">
        <p className="text-[0.95rem] text-text">
          Looking for the bank&apos;s reply
          <span aria-hidden="true" className="inline-flex w-5">
            <span className="motion-safe:animate-[mail-dot_1.4s_ease-in-out_infinite]">.</span>
            <span className="motion-safe:animate-[mail-dot_1.4s_ease-in-out_0.2s_infinite]">.</span>
            <span className="motion-safe:animate-[mail-dot_1.4s_ease-in-out_0.4s_infinite]">.</span>
          </span>
        </p>
        <p className="text-sm text-text-3">
          Watching this email&apos;s thread in your Gmail{ago ? `, last checked ${ago}` : ""}. A reply there is
          checked against the rules as soon as it arrives.
        </p>
      </div>
    </div>
  );
}

function WaitingForBank({ c, refresh }: Props) {
  const esc = c.escalation;
  const until = esc?.waiting_period_ends;
  const days = until ? daysFromToday(until) : null;
  const deadline = until
    ? `Your bank has until ${formatDate(until, { long: true })} to put it right${
        days !== null && days >= 0 ? `, ${days === 0 ? "today" : `${days} days from now`}` : ""
      }.`
    : "";
  return (
    <div className="flex flex-col gap-6">
      <Heading title="Waiting for your bank">
        {c.watching_thread
          ? `${deadline} Recourse is looking out for the reply, so there's nothing you need to do.`
          : `${deadline} When it replies, paste the reply here and Recourse checks it against the rules.`}
      </Heading>
      {c.watching_thread ? <MailWatch c={c} /> : null}
      {c.bank_complaint_ref ? (
        <p className="text-sm text-text-3">
          Bank&apos;s complaint number: <span className="text-text-2">{c.bank_complaint_ref}</span>
        </p>
      ) : null}
      {c.last_reply ? <LastReply r={c.last_reply} /> : null}
      <FormError message={c.last_error} />
      <div className="flex flex-col items-start gap-4">
        <ReplyForm c={c} refresh={refresh} fallback={c.watching_thread} />
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

export function StopCase({ c, refresh }: Props) {
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

function Body({ c, refresh, step }: Props & { step: StepKey }) {
  switch (step) {
    case "ended":
      return <Ended c={c} />;
    case "reading":
      return <Working>Reading your evidence. This takes a few seconds.</Working>;
    case "review_facts":
      return <ReviewFacts c={c} onDone={refresh} />;
    case "bank_type":
      return <BankTypeForm c={c} refresh={refresh} />;
    case "recheck_escalation":
      return (
        <div className="flex flex-col gap-5">
          <Heading title="The Ombudsman complaint can't be prepared yet">{c.last_error}</Heading>
          <div>
            <QuietButton onClick={async () => { await api.recheckEscalation(c.id); refresh(); }}>Check again</QuietButton>
          </div>
        </div>
      );
    case "redraft":
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
    case "review_letter":
      return <LetterReview key={c.draft!.id} c={c} draft={c.draft!} onDone={refresh} />;
    case "blocked_send":
      return <BlockedSend c={c} refresh={refresh} />;
    case "gmail_retry":
      return <GmailRetry c={c} refresh={refresh} />;
    case "gmail_sending":
      return <Working>Sending the letter from your Gmail.</Working>;
    case "send_yourself":
      return <SendYourself c={c} refresh={refresh} />;
    case "file_ombudsman":
      return <FilingPackage c={c} refresh={refresh} />;
    case "close":
      return <CloseForm c={c} refresh={refresh} />;
    case "decision":
      return <Decision c={c} refresh={refresh} />;
    case "preparing_ombudsman":
      return <Working>Preparing your complaint to the RBI Ombudsman. Every figure is checked first.</Working>;
    case "waiting_bank":
      return <WaitingForBank c={c} refresh={refresh} />;
    case "not_covered":
      return (
        <Heading title="These rules don't cover this case">
          See what the rules say beside this panel. You can still contact your bank directly.
        </Heading>
      );
    case "drafting":
      return <Working>Writing your letter and checking every figure in it.</Working>;
    case "starting_wait":
      return <Working>Starting the bank&apos;s reply window.</Working>;
    default:
      return <Working>Working on your case.</Working>;
  }
}

/** With the Ombudsman, the bank can still answer or pay: show late replies, and let the user
 * add one or close the case as resolved, without leaving the Ombudsman steps. */
function BankCanStillAnswer({ c, refresh }: Props) {
  return (
    <div className="flex flex-col gap-4 border-t border-line pt-6">
      <p className="text-sm text-text-2">
        Your bank can still reply or pay while this goes to the Ombudsman. A late reply is checked
        and shown here; it doesn&apos;t change your Ombudsman steps.
      </p>
      {c.watching_thread ? <MailWatch c={c} /> : null}
      {c.last_reply ? <LastReply r={c.last_reply} /> : null}
      <div className="flex flex-col items-start gap-3">
        <ReplyForm c={c} refresh={refresh} fallback={c.watching_thread} />
        <ResolvedForm c={c} refresh={refresh} />
      </div>
    </div>
  );
}

const OWNER_DOT: Record<Owner, string> = {
  you: "bg-blue",
  recourse: "bg-blue motion-safe:animate-[pulse_2.4s_ease-in-out_infinite]",
  bank: "bg-text-2",
  done: "bg-text-3",
};

/**
 * The one place to act. Its frame never changes: a header with the step and whose turn it is,
 * then the step's own content. Only the content below the header changes as the case moves.
 */
export function NextAction({ c, refresh }: Props) {
  const ended = c.status === "RESOLVED" || c.status === "CLOSED" || c.status === "ABANDONED";
  const late = c.can_resolve && !["AWAITING_BANK", "JUDGED", "SENT_TO_BANK"].includes(c.status);
  const step = nextStep(c);
  const stage = currentStage(c);
  return (
    <Panel lit aria-labelledby="next-action-title" className="flex flex-col p-0 sm:p-0">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line px-5 py-3.5 sm:px-6">
        <h2 id="next-action-title" className="text-sm text-text-2">
          Step <span className="figures">{stage + 1}</span> of <span className="figures">{STAGES.length}</span>:{" "}
          <span className="text-text">{STAGES[stage].label}</span>
        </h2>
        <span className="inline-flex items-center gap-2 text-sm text-text-2">
          <span aria-hidden="true" className={cn("size-1.5 rounded-full", OWNER_DOT[step.owner])} />
          {OWNER_LABEL[step.owner]}
        </span>
      </div>
      <div className="flex flex-col gap-8 px-5 py-6 sm:px-6 sm:py-7">
        <Body c={c} refresh={refresh} step={step.key} />
        {late && !ended ? <BankCanStillAnswer c={c} refresh={refresh} /> : null}
      </div>
    </Panel>
  );
}
