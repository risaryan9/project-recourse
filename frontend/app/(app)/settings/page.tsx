"use client";

import { useState } from "react";

import { FormError, Panel, QuietButton, TextButton } from "@/components/app/ui";
import { api } from "@/lib/api";
import { useConfig } from "@/lib/hooks";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Panel className="flex flex-col gap-3">
      <h2 className="type-heading">{title}</h2>
      {children}
    </Panel>
  );
}

export default function SettingsPage() {
  const config = useConfig();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleted, setDeleted] = useState<number | null>(null);

  async function deleteAll() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.deleteMyCases();
      setDeleted(r.deleted);
      setConfirming(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl pt-8 sm:pt-12">
      <h1 className="type-title">Settings</h1>
      <div className="mt-10 flex flex-col gap-5">
        <Section title="Email">
          <p className="text-text-2">
            You send each approved letter from your own email, and paste the bank&apos;s reply back
            into the case. Recourse never sends anything without your approval.
          </p>
          {config?.email_sandbox ? (
            <p className="text-text-2">
              <span className="text-text">Sandbox is on.</span> Letters are addressed to a test
              inbox, never to a real bank.
            </p>
          ) : null}
          <p className="text-sm text-text-3">
            Connecting Gmail, so Recourse can send approved letters and watch for the bank&apos;s
            reply in that one thread, is coming later.
          </p>
        </Section>

        {config?.demo_mode ? (
          <Section title="Demo mode">
            <p className="text-text-2">
              Time runs fast: one day in a case lasts {config.demo_day_seconds} seconds, so a bank&apos;s
              30-day window passes in a few minutes.
            </p>
          </Section>
        ) : null}

        <Section title="Your data">
          <p className="text-text-2">
            Your cases belong to this browser. Deleting them stops every running case and removes
            its evidence, letters, replies and timeline.
          </p>
          {deleted !== null ? (
            <p role="status" className="text-text">
              {deleted === 0 ? "There was nothing to delete." : `Deleted ${deleted} ${deleted === 1 ? "case" : "cases"}.`}
            </p>
          ) : null}
          <FormError message={error} />
          {confirming ? (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
              <span className="text-text-2">Delete all your cases? This can&apos;t be undone.</span>
              <QuietButton busy={busy} onClick={deleteAll} className="text-destructive">
                Delete everything
              </QuietButton>
              <TextButton onClick={() => setConfirming(false)}>Cancel</TextButton>
            </div>
          ) : (
            <div>
              <QuietButton onClick={() => setConfirming(true)}>Delete my data</QuietButton>
            </div>
          )}
        </Section>
      </div>
    </div>
  );
}
