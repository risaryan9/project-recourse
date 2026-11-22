"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Attachments } from "@/components/app/attachments";
import { KeyPanel } from "@/components/app/key-panel";
import { FormError, Notice, Panel, PrimaryButton } from "@/components/app/ui";
import { api } from "@/lib/api";
import { useKeyStatus } from "@/lib/hooks";

const ACCEPT = "image/png,image/jpeg,image/webp,image/gif,application/pdf";
const ACCEPTED = new Set(ACCEPT.split(","));
const MAX_FILES = 5;
const MAX_BYTES = 20 * 1024 * 1024;

export default function NewCasePage() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const { status: keys, refresh: refreshKeys } = useKeyStatus();
  const needsKey = !!keys?.required && !keys.anthropic && !keys.openai;

  const [dragging, setDragging] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const pasteCount = useRef(0); // pasted-screenshot-1.png, -2, … (the clipboard calls them all image.png)

  /** Add files from the picker, a paste or a drop; skip types Recourse can't read. */
  const addFiles = useCallback((incoming: File[], from: "paste" | "drop" | "pick") => {
    const ok = incoming.filter((f) => ACCEPTED.has(f.type));
    const skipped = incoming.length - ok.length;
    if (ok.length) {
      // Screenshots pasted from the clipboard all arrive as "image.png": number them clearly.
      // (Named here, not inside the state update, which React may run twice.)
      const named = ok.map((f) =>
        from === "paste" && /^image\.\w+$/i.test(f.name)
          ? new File([f], `pasted-screenshot-${++pasteCount.current}.${f.type.split("/")[1] || "png"}`, {
              type: f.type,
            })
          : f,
      );
      setFiles((cur) => [...cur, ...named]);
    }
    setNotice(
      skipped
        ? `${skipped === 1 ? "One file was" : `${skipped} files were`} skipped: use PNG, JPEG, WebP, GIF or PDF.`
        : from === "paste" && ok.length
          ? `Pasted ${ok.length === 1 ? "a screenshot" : `${ok.length} images`}.`
          : null,
    );
  }, []);

  // Paste a screenshot anywhere on the page (Ctrl+V / Cmd+V). Text pastes are left alone.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const pasted = Array.from(e.clipboardData?.files ?? []);
      if (pasted.length === 0) return;
      e.preventDefault();
      addFiles(pasted, "paste");
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [addFiles]);

  const total = files.reduce((n, f) => n + f.size, 0);
  const tooMany = files.length > MAX_FILES;
  const tooBig = total > MAX_BYTES;
  const empty = !text.trim() && files.length === 0;

  async function submit() {
    setError(null);
    setBusy(true);
    const form = new FormData();
    if (text.trim()) form.append("text", text.trim());
    files.forEach((f) => form.append("files", f));
    try {
      const created = await api.createCase(form);
      router.push(`/cases/${created.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl pt-8 sm:pt-12">
      <h1 className="type-title">Start a case</h1>
      <p className="type-lede mt-3 max-w-[48ch]">
        Share what happened. Recourse reads the amount, date and reference, then asks you to check
        anything it is unsure of.
      </p>

      {needsKey ? (
        <Panel lit className="mt-10 flex flex-col gap-4">
          <h2 className="type-heading">First, add your AI key</h2>
          <KeyPanel status={keys} onChange={refreshKeys} />
        </Panel>
      ) : null}

      <Panel lit={!needsKey} className={needsKey ? "mt-6 opacity-60" : "mt-10"}>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!empty && !tooMany && !tooBig) submit();
          }}
          className="flex flex-col gap-6"
        >
          <div>
            <label htmlFor="text" className="font-[560] text-text">
              The bank&apos;s SMS, or what happened in your words
            </label>
            <textarea
              id="text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={6}
              maxLength={20000}
              placeholder="Rs 2,450.00 debited from A/c XX1234 via UPI on 14-11-26 18:05, UPI Ref No 631845207719. Transaction failed."
              className="mt-2 w-full resize-y rounded-xl border border-line-strong bg-white/[0.03] px-3.5 py-3 text-[0.95rem] leading-relaxed text-text outline-none placeholder:text-text-3 focus-visible:border-blue focus-visible:ring-2 focus-visible:ring-blue/30"
            />
          </div>

          <div>
            <div className="font-[560] text-text">Screenshots or a statement</div>
            <p className="mt-1 text-sm text-text-3">
              PNG, JPEG, WebP or PDF. Up to {MAX_FILES} files, 20 MB in total.
            </p>
            <input
              ref={input}
              id="files"
              type="file"
              multiple
              accept={ACCEPT}
              className="sr-only"
              onChange={(e) => {
                // Read the picked files before clearing the input: the state update runs
                // later, when e.target.files would already be empty.
                const picked = Array.from(e.target.files ?? []);
                e.target.value = "";
                addFiles(picked, "pick");
              }}
            />
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                addFiles(Array.from(e.dataTransfer.files), "drop");
              }}
              className={
                "mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-[1rem] border border-dashed px-4 py-4 transition-colors " +
                (dragging ? "border-blue/70 bg-blue/[0.07]" : "border-line-strong bg-white/[0.015]")
              }
            >
              <label htmlFor="files" className="action-quiet h-10 cursor-pointer text-sm focus-within:ring-2">
                Add files
              </label>
              <span className="text-sm text-text-3">
                or paste a screenshot (<kbd className="font-sans text-text-2">Ctrl</kbd>+
                <kbd className="font-sans text-text-2">V</kbd>), or drop files here
              </span>
            </div>
            {notice ? (
              <p role="status" className="mt-2 text-sm text-text-2">
                {notice}
              </p>
            ) : null}
            <Attachments files={files} onRemove={(i) => setFiles((cur) => cur.filter((_, j) => j !== i))} />
            {tooMany ? <FormError message={`Choose at most ${MAX_FILES} files.`} /> : null}
            {tooBig ? <FormError message="These files are over 20 MB in total. Remove one." /> : null}
          </div>

          <Notice title="Keep your codes to yourself">
            Never share an OTP, PIN, CVV or password, here or anywhere. If one is in your SMS,
            Recourse removes it before anything is stored. Account and card numbers are masked to
            the last four digits.
          </Notice>

          <FormError message={error} />

          <div className="flex flex-wrap items-center gap-4">
            <PrimaryButton type="submit" busy={busy} disabled={empty || tooMany || tooBig || needsKey}>
              Read my evidence
            </PrimaryButton>
          </div>
        </form>
      </Panel>
    </div>
  );
}
