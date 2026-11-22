"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { FormError, Panel, QuietButton, TextButton } from "@/components/app/ui";
import { api, GMAIL_CONNECT_URL } from "@/lib/api";
import { GmailLogo } from "@/components/brand/gmail-logo";
import { useConfig, useGmail } from "@/lib/hooks";

const GMAIL_ERRORS: Record<string, string> = {
  denied: "You cancelled the Google sign-in. Nothing was connected.",
  scopes_missing: "Recourse needs both Gmail permissions (send, and read the complaint's thread). Connect again and allow both.",
  state_mismatch: "The sign-in expired or came from another tab. Connect again from this page.",
  no_owner: "Your browser blocked Recourse's cookie. Allow cookies for this site and connect again.",
  exchange_failed: "Google refused the sign-in. Try again in a moment.",
  no_refresh_token: "Google didn't grant lasting access. Remove Recourse at myaccount.google.com/permissions, then connect again.",
  email_unverified: "That Google account has no verified email address.",
  bad_id_token: "Google's answer couldn't be checked. Try again.",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Panel className="flex flex-col gap-3">
      <h2 className="type-heading">{title}</h2>
      {children}
    </Panel>
  );
}

function Settings() {
  const config = useConfig();
  const { status: gmail, refresh: refreshGmail } = useGmail();
  const params = useSearchParams();
  const router = useRouter();
  const [actionNote, setActionNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);

  // Google sends the browser back here with ?gmail=connected or ?gmail=error&reason=...
  const result = params.get("gmail");
  const urlNote = result
    ? result === "connected"
      ? { ok: true, text: "Gmail is connected." }
      : { ok: false, text: GMAIL_ERRORS[params.get("reason") ?? ""] ?? "Gmail could not be connected. Try again." }
    : null;
  // Kept after the URL is tidied below (this component renders only in the browser).
  const [returnNote] = useState(urlNote);
  const gmailNote = actionNote ?? returnNote;
  useEffect(() => {
    if (result) router.replace("/settings", { scroll: false });
  }, [result, router]);

  async function disconnectGmail() {
    setDisconnecting(true);
    try {
      await api.gmailDisconnect();
      setActionNote({ ok: true, text: "Gmail is disconnected. Recourse's access was revoked and its token deleted." });
    } catch (e) {
      setActionNote({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setDisconnecting(false);
      refreshGmail();
    }
  }
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
          {gmail?.connected ? (
            <p className="text-text-2">
              <GmailLogo className="mr-2 inline h-3.5 w-auto align-[-0.1em]" />
              <span className="text-text">Gmail is connected</span> as {gmail.email}. When you approve
              a letter, Recourse sends it from this account and watches that one email thread for
              the bank&apos;s reply.
            </p>
          ) : (
            <p className="text-text-2">
              You send each approved letter from your own email, and paste the bank&apos;s reply back
              into the case.
              {gmail?.configured ? " Or connect Gmail, and Recourse sends approved letters and watches for the reply for you." : ""}
            </p>
          )}
          <p className="text-sm text-text-3">
            Recourse never sends anything you haven&apos;t approved. With Gmail it reads only the
            thread it started, and every Gmail call shows on the case&apos;s timeline.
          </p>
          {config?.email_sandbox ? (
            <p className="text-text-2">
              <span className="text-text">Sandbox is on.</span> Letters are addressed to a test
              inbox, never to a real bank.
            </p>
          ) : null}
          {gmailNote ? (
            <p role="status" className={gmailNote.ok ? "text-text" : "text-destructive"}>
              {gmailNote.text}
            </p>
          ) : null}
          {gmail?.configured ? (
            gmail.connected ? (
              <div>
                <QuietButton busy={disconnecting} onClick={disconnectGmail}>
                  Disconnect Gmail
                </QuietButton>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                <a href={GMAIL_CONNECT_URL} className="action-quiet w-fit gap-2.5">
                  <GmailLogo className="h-3.5 w-auto" />
                  Connect Gmail
                </a>
                <p className="text-sm text-text-3">
                  Google will warn that the app isn&apos;t verified yet; choose Advanced, then
                  continue. Recourse asks to send email and to read email, and uses the reading
                  only for the complaint&apos;s own thread.
                </p>
              </div>
            )
          ) : (
            <p className="text-sm text-text-3">Gmail isn&apos;t set up on this server.</p>
          )}
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

export default function SettingsPage() {
  // useSearchParams needs a Suspense boundary on a prerendered page (Next.js 16).
  return (
    <Suspense fallback={null}>
      <Settings />
    </Suspense>
  );
}
