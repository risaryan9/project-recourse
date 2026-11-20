"use client";

import { useState, type ButtonHTMLAttributes, type ReactNode } from "react";

import type { CaseStatus } from "@/lib/api";
import { STATUS } from "@/lib/labels";
import { cn } from "@/lib/utils";

/** A glass panel. `lit` for the one that matters most on a screen (the Next action). */
export function Panel({
  lit = false,
  className,
  children,
  as: Tag = "section",
  ...rest
}: {
  lit?: boolean;
  className?: string;
  children: ReactNode;
  as?: "section" | "div" | "article";
} & React.HTMLAttributes<HTMLElement>) {
  return (
    <Tag
      className={cn("glass rounded-[1.5rem] p-5 sm:p-6", lit && "glass-lit", className)}
      {...rest}
    >
      {children}
    </Tag>
  );
}

export function StatusPill({ status, className }: { status: CaseStatus; className?: string }) {
  const s = STATUS[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full border border-line px-3 py-1 text-sm whitespace-nowrap text-text-2",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn("size-1.5 rounded-full", s.live ? "bg-blue" : "bg-text-3")}
      />
      {s.label}
    </span>
  );
}

/** Shown while the case engine works on its own (reading, drafting, checking). */
export function Working({ children }: { children: ReactNode }) {
  return (
    <div role="status" className="flex items-center gap-3 text-text-2">
      <span aria-hidden="true" className="relative flex size-2">
        <span className="absolute inline-flex size-full rounded-full bg-blue opacity-60 motion-safe:animate-[pulse_2.4s_ease-in-out_infinite]" />
        <span className="relative inline-flex size-2 rounded-full bg-blue" />
      </span>
      <span>{children}</span>
    </div>
  );
}

export function Notice({
  title,
  children,
  tone = "info",
  className,
}: {
  title?: string;
  children?: ReactNode;
  tone?: "info" | "warn";
  className?: string;
}) {
  return (
    <div
      role={tone === "warn" ? "alert" : undefined}
      className={cn(
        "rounded-2xl border p-4",
        tone === "warn" ? "border-amber/30 bg-amber/[0.06]" : "border-line bg-white/[0.02]",
        className,
      )}
    >
      {title ? (
        <div className={cn("font-[560]", tone === "warn" ? "text-amber" : "text-text")}>{title}</div>
      ) : null}
      {children ? <div className={cn("text-sm leading-relaxed text-text-2", title && "mt-1")}>{children}</div> : null}
    </div>
  );
}

export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="text-sm text-destructive">
      {message}
    </p>
  );
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean };

/** The key action of a screen. One per screen. */
export function PrimaryButton({ busy, disabled, className, children, ...rest }: BtnProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || busy}
      className={cn(
        "action-primary h-11 px-5 text-[0.95rem] disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
    >
      {busy ? "Working…" : children}
    </button>
  );
}

export function QuietButton({ busy, disabled, className, children, ...rest }: BtnProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || busy}
      className={cn("action-quiet h-11 disabled:cursor-not-allowed disabled:opacity-50", className)}
    >
      {busy ? "Working…" : children}
    </button>
  );
}

export function TextButton({ className, children, ...rest }: BtnProps) {
  return (
    <button
      type="button"
      {...rest}
      className={cn(
        "text-[0.95rem] text-text-2 underline-offset-4 transition-colors hover:text-text hover:underline disabled:opacity-50",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <QuietButton
      className="h-9 px-3.5 text-sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1800);
        } catch {
          setDone(false);
        }
      }}
    >
      {done ? "Copied" : label}
    </QuietButton>
  );
}

/** Label/value rows (facts, filing fields). */
export function Rows({ rows, className }: { rows: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("divide-y divide-line", className)}>
      {rows.map(([k, v], i) => (
        <div key={i} className="flex items-baseline justify-between gap-6 py-2.5">
          <dt className="text-sm text-text-3">{k}</dt>
          <dd className="text-right text-[0.95rem] text-text">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export const fieldClass =
  "h-11 w-full rounded-xl border border-line-strong bg-white/[0.03] px-3.5 text-[0.95rem] text-text placeholder:text-text-3 outline-none transition-colors focus-visible:border-blue focus-visible:ring-2 focus-visible:ring-blue/30";
