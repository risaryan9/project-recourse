"use client";

import { useState } from "react";

import { fieldClass, FormError, Notice, PrimaryButton, QuietButton, TextButton } from "@/components/app/ui";
import { api, type CaseView, type Draft } from "@/lib/api";

const NAME_PLACEHOLDER = "[Your full name]";

/** Read, edit and approve a drafted letter (to the bank, or to the RBI Ombudsman). */
export function LetterReview({ c, draft, onDone }: { c: CaseView; draft: Draft; onDone: () => void }) {
  const ombudsman = draft.kind === "OMBUDSMAN_COMPLAINT";
  const [name, setName] = useState("");
  const [subject, setSubject] = useState(draft.subject ?? "");
  const [body, setBody] = useState(draft.body ?? "");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function approve() {
    setError(null);
    if (name.trim().length < 2) {
      setError("Type your full name; it signs the letter.");
      return;
    }
    setBusy("approve");
    try {
      await api.approveDraft(c.id, draft.id, {
        customer_name: name.trim(),
        subject: subject !== draft.subject ? subject : undefined,
        body: body !== draft.body ? body : undefined,
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function reject() {
    setBusy("reject");
    setError(null);
    try {
      await api.rejectDraft(c.id, draft.id, reason.trim());
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="type-heading">
          {ombudsman ? "Your complaint to the RBI Ombudsman" : "Your letter to the bank"}
        </h2>
        <p className="mt-1.5 text-text-2">
          Every amount, date and rule in it was checked against what the rules say you&apos;re owed.
          Read it, change anything you like, and sign it with your name.
          {ombudsman ? " You'll file it yourself; nothing is sent." : " Nothing is sent until you approve it."}
        </p>
      </div>

      <div className="flex flex-col gap-4">
        <div>
          <label htmlFor="subject" className="text-sm text-text-2">
            {ombudsman ? "Title" : "Subject"}
          </label>
          <input
            id="subject"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            className={`${fieldClass} mt-1.5`}
          />
        </div>
        <div>
          <label htmlFor="body" className="text-sm text-text-2">
            {ombudsman ? "Complaint" : "Letter"}
          </label>
          <textarea
            id="body"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={16}
            className="mt-1.5 w-full resize-y rounded-xl border border-line-strong bg-white/[0.03] px-4 py-3.5 text-[0.95rem] leading-relaxed text-text outline-none focus-visible:border-blue focus-visible:ring-2 focus-visible:ring-blue/30"
          />
          <p className="mt-1.5 text-sm text-text-3">
            If you change an amount or a date, it must still match the rules; Recourse checks again
            before approving. {NAME_PLACEHOLDER} is replaced by your name.
          </p>
        </div>
        <div>
          <label htmlFor="name" className="text-sm text-text-2">
            Your full name
          </label>
          <input
            id="name"
            value={name}
            autoComplete="name"
            onChange={(e) => setName(e.target.value)}
            className={`${fieldClass} mt-1.5 max-w-sm`}
          />
        </div>
      </div>

      {!draft.rules_verified ? (
        <Notice>
          The rules this letter cites are quoted from the RBI&apos;s documents; a final human check of
          them is still pending. {draft.disclaimer}
        </Notice>
      ) : (
        <p className="text-sm text-text-3">{draft.disclaimer}</p>
      )}

      <FormError message={error ?? c.last_error} />

      {rejecting ? (
        <div className="flex flex-col gap-3">
          <label htmlFor="reason" className="text-sm text-text-2">
            What should be different? (optional)
          </label>
          <textarea
            id="reason"
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            className="w-full rounded-xl border border-line-strong bg-white/[0.03] px-3.5 py-3 text-[0.95rem] text-text outline-none focus-visible:border-blue"
          />
          <div className="flex flex-wrap gap-3">
            <QuietButton busy={busy === "reject"} onClick={reject}>
              Write a new draft
            </QuietButton>
            <TextButton onClick={() => setRejecting(false)}>Keep this one</TextButton>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <PrimaryButton busy={busy === "approve"} onClick={approve}>
            {ombudsman ? "Approve complaint" : "Approve letter"}
          </PrimaryButton>
          <TextButton onClick={() => setRejecting(true)}>Ask for a new draft</TextButton>
        </div>
      )}
    </div>
  );
}
