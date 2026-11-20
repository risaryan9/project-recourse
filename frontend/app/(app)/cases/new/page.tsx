"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { FormError, Notice, Panel, PrimaryButton, TextButton } from "@/components/app/ui";
import { api } from "@/lib/api";

const ACCEPT = "image/png,image/jpeg,image/webp,image/gif,application/pdf";
const MAX_FILES = 5;
const MAX_BYTES = 20 * 1024 * 1024;

export default function NewCasePage() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

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

      <Panel lit className="mt-10">
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
                setFiles((cur) => [...cur, ...Array.from(e.target.files ?? [])]);
                e.target.value = "";
              }}
            />
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <label
                htmlFor="files"
                className="action-quiet h-10 cursor-pointer text-sm focus-within:ring-2"
              >
                Add files
              </label>
              {files.map((f, i) => (
                <span
                  key={`${f.name}-${i}`}
                  className="inline-flex items-center gap-2 rounded-full border border-line px-3 py-1.5 text-sm text-text-2"
                >
                  <span className="max-w-[14rem] truncate">{f.name}</span>
                  <TextButton
                    className="text-sm"
                    aria-label={`Remove ${f.name}`}
                    onClick={() => setFiles((cur) => cur.filter((_, j) => j !== i))}
                  >
                    Remove
                  </TextButton>
                </span>
              ))}
            </div>
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
            <PrimaryButton type="submit" busy={busy} disabled={empty || tooMany || tooBig}>
              Read my evidence
            </PrimaryButton>
          </div>
        </form>
      </Panel>
    </div>
  );
}
