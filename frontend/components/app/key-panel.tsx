"use client";

import { useState } from "react";

import { fieldClass, FormError, PrimaryButton, TextButton } from "@/components/app/ui";
import { api, type KeyStatus } from "@/lib/api";
import { clearKeys, readKeys, writeKeys } from "@/lib/keys";

const REPO = "https://github.com/risaryan9/project-recourse";

function hoursLeft(s: KeyStatus): string {
  const h = Math.max(1, Math.round((s.expires_in_seconds ?? 0) / 3600));
  return `${h} ${h === 1 ? "hour" : "hours"}`;
}

/** Bring your own key (ADR-023): enter, check and forget the visitor's AI key. */
export function KeyPanel({ status, onChange }: { status: KeyStatus | null; onChange: () => void }) {
  const local = readKeys();
  const [anthropic, setAnthropic] = useState("");
  const [openai, setOpenai] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = !!status && (status.anthropic || status.openai);

  async function save() {
    setBusy(true);
    setError(null);
    const keys = { anthropic_key: anthropic.trim() || undefined, openai_key: openai.trim() || undefined };
    try {
      await api.saveKeys(keys);
      writeKeys(keys);
      setAnthropic("");
      setOpenai("");
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function forget() {
    setBusy(true);
    try {
      await api.forgetKeys();
    } finally {
      clearKeys();
      setBusy(false);
      onChange();
    }
  }

  return (
    <div className="flex flex-col gap-5">
      {active ? (
        <p className="text-text-2">
          <span className="text-text">Your key is in use</span>
          {status.anthropic && status.openai ? " (Anthropic and OpenAI)" : status.anthropic ? " (Anthropic)" : " (OpenAI)"}.
          The server forgets it in {hoursLeft(status)}, or as soon as you choose Forget.
          {local ? "" : " This tab doesn't hold a copy, so you'll enter it again after that."}
        </p>
      ) : (
        <p className="text-text-2">
          Recourse runs on your own Anthropic or OpenAI API key, so each case is paid for by the
          person who starts it (usually a few cents). Add an Anthropic key, an OpenAI key, or both.
        </p>
      )}

      {!active ? (
        <div className="flex flex-col gap-4">
          <div>
            <label htmlFor="anthropic-key" className="text-sm text-text-2">
              Anthropic API key
            </label>
            <input
              id="anthropic-key"
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder="sk-ant-…"
              value={anthropic}
              onChange={(e) => setAnthropic(e.target.value)}
              className={`${fieldClass} mt-1.5`}
            />
          </div>
          <div>
            <label htmlFor="openai-key" className="text-sm text-text-2">
              OpenAI API key <span className="text-text-3">(optional, used if Anthropic is unavailable)</span>
            </label>
            <input
              id="openai-key"
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder="sk-…"
              value={openai}
              onChange={(e) => setOpenai(e.target.value)}
              className={`${fieldClass} mt-1.5`}
            />
          </div>
          <FormError message={error} />
          <div>
            <PrimaryButton busy={busy} disabled={!anthropic.trim() && !openai.trim()} onClick={save}>
              Check and use my key
            </PrimaryButton>
          </div>
        </div>
      ) : (
        <div>
          <TextButton className="text-sm" onClick={forget} disabled={busy}>
            Forget my key now
          </TextButton>
        </div>
      )}

      <div className="rounded-2xl border border-line bg-white/[0.02] p-4 text-sm leading-relaxed text-text-2">
        <span className="font-[560] text-text">Your key is never saved.</span> It is not written to a
        database, a file, a log or your case&apos;s history. It stays in this browser tab (gone when
        you close it), and the server keeps it in memory only while your case needs it, for up to
        six hours. It is used only for your own case&apos;s AI steps. Don&apos;t take our word for it:{" "}
        <a href={REPO} target="_blank" rel="noreferrer" className="text-text underline underline-offset-4">
          the code is open source
        </a>
        .
      </div>
    </div>
  );
}
