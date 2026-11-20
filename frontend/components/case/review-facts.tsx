"use client";

import { useMemo, useState } from "react";

import { fieldClass, FormError, Notice, PrimaryButton, TextButton } from "@/components/app/ui";
import { api, type CaseView } from "@/lib/api";
import { formatDateTime, inr } from "@/lib/format";
import { DETAILS_LABEL, ENUMS, FACTS } from "@/lib/labels";
import { cn } from "@/lib/utils";

/* ISO date-time <-> the value of an <input type="datetime-local"> in IST. */
function toLocalIST(iso: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date(iso));
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "00";
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour") === "24" ? "00" : get("hour")}:${get("minute")}`;
}
const fromLocalIST = (local: string) => (local ? `${local}:00+05:30` : null);

function display(kind: string, name: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not found";
  if (kind === "money") return inr(String(value));
  if (kind === "datetime") return formatDateTime(String(value));
  if (kind === "bool") return value ? "Yes" : "No";
  if (kind === "enum") return ENUMS[name]?.[String(value)] ?? String(value);
  return String(value);
}

function initial(kind: string, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (kind === "datetime") return toLocalIST(String(value));
  if (kind === "bool") return value ? "true" : "false";
  return String(value);
}

function toValue(kind: string, raw: string): unknown {
  if (raw === "") return null;
  if (kind === "datetime") return fromLocalIST(raw);
  if (kind === "bool") return raw === "true";
  return raw;
}

function FactInput({
  id,
  name,
  kind,
  value,
  onChange,
}: {
  id: string;
  name: string;
  kind: string;
  value: string;
  onChange: (v: string) => void;
}) {
  if (kind === "enum" || kind === "bool") {
    const options =
      kind === "bool" ? { true: "Yes", false: "No" } : (ENUMS[name] ?? ({} as Record<string, string>));
    return (
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={fieldClass}>
        <option value="">Choose…</option>
        {Object.entries(options).map(([k, label]) => (
          <option key={k} value={k}>
            {label}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      id={id}
      type={kind === "datetime" ? "datetime-local" : "text"}
      inputMode={kind === "money" ? "decimal" : undefined}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={fieldClass}
    />
  );
}

export function ReviewFacts({ c, onDone }: { c: CaseView; onDone: () => void }) {
  const ev = c.evidence!;
  const needs = new Set(ev.needs_confirmation);
  const issues = new Map(ev.issues.map((i) => [i.field, i.message]));
  const fraud = ev.facts.case_type?.value === "fraud";

  const facts = useMemo(
    () => FACTS.filter((f) => f.name in ev.facts && (ev.facts[f.name].value !== null || needs.has(f.name))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [ev],
  );
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(FACTS.map((f) => [f.name, initial(f.kind, ev.facts[f.name]?.value)])),
  );
  const [editing, setEditing] = useState<Set<string>>(() => new Set(needs));
  const [details, setDetails] = useState<Record<string, string>>(() => ({
    bank_type: c.details.bank_type ?? "",
    account_type: c.details.account_type ?? "",
    fault: c.details.fault ?? "UNKNOWN",
    reported_to_bank_date: c.details.reported_to_bank_date ?? "",
    reported_to_ncrp_date: c.details.reported_to_ncrp_date ?? "",
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    // Every field asked about is sent back (confirming it), plus any the user changed.
    const corrections: Record<string, unknown> = {};
    for (const f of FACTS) {
      const changed = values[f.name] !== initial(f.kind, ev.facts[f.name]?.value);
      if (needs.has(f.name) || (editing.has(f.name) && changed)) {
        corrections[f.name] = toValue(f.kind, values[f.name]);
      }
    }
    const missing = [...needs].filter((n) => corrections[n] === null);
    if (missing.length) {
      setError(`Fill in: ${missing.map((n) => FACTS.find((f) => f.name === n)?.label ?? n).join(", ")}.`);
      return;
    }
    const det: Record<string, string | null> = {};
    if (fraud) {
      for (const [k, v] of Object.entries(details)) det[k] = v || null;
      det.fault = details.fault || "UNKNOWN";
      if (!det.bank_type) {
        setError("Choose what kind of bank it is.");
        return;
      }
    }
    setBusy(true);
    try {
      await api.confirm(c.id, { corrections, details: fraud ? det : undefined });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="type-heading">Check the details</h2>
        <p className="mt-1.5 text-text-2">
          {needs.size
            ? "Recourse read these from your evidence. Check the highlighted ones; the amount owed depends on them."
            : "Recourse read these from your evidence. If they're right, confirm them."}
        </p>
      </div>

      {ev.redactions.length ? (
        <Notice title="Sensitive details removed">
          Your evidence contained {ev.redactions.length === 1 ? "a code or number" : "codes or numbers"} that
          should never be shared. Recourse removed them before storing anything.
        </Notice>
      ) : null}

      <ul className="divide-y divide-line border-y border-line">
        {facts.map((f) => {
          const flagged = needs.has(f.name);
          const open = editing.has(f.name);
          const fact = ev.facts[f.name];
          const id = `fact-${f.name}`;
          return (
            <li key={f.name} className={cn("py-3.5", flagged && "-mx-3 rounded-xl bg-blue/[0.06] px-3")}>
              <div className="flex items-baseline justify-between gap-4">
                <label htmlFor={open ? id : undefined} className="text-sm text-text-2">
                  {f.label}
                </label>
                {!open ? (
                  <div className="flex items-baseline gap-3 text-right">
                    <span className="text-[0.95rem] text-text">{display(f.kind, f.name, fact?.value)}</span>
                    <TextButton
                      className="text-sm"
                      onClick={() => setEditing((s) => new Set(s).add(f.name))}
                      aria-label={`Change ${f.label}`}
                    >
                      Change
                    </TextButton>
                  </div>
                ) : null}
              </div>
              {open ? (
                <div className="mt-2">
                  <FactInput
                    id={id}
                    name={f.name}
                    kind={f.kind}
                    value={values[f.name]}
                    onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))}
                  />
                  {flagged ? (
                    <p className="mt-1.5 text-sm text-blue-bright">
                      {issues.get(f.name) ?? "Please check this."}
                      {fact?.source_text ? ` From your evidence: "${fact.source_text}"` : ""}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>

      {fraud ? (
        <div className="flex flex-col gap-4">
          <p className="text-text-2">A few things an SMS can&apos;t show:</p>
          {(["bank_type", "account_type", "fault"] as const).map((k) => (
            <div key={k}>
              <label htmlFor={`d-${k}`} className="text-sm text-text-2">
                {DETAILS_LABEL[k]}
                {k === "bank_type" ? "" : " (optional)"}
              </label>
              <select
                id={`d-${k}`}
                value={details[k]}
                onChange={(e) => setDetails((s) => ({ ...s, [k]: e.target.value }))}
                className={cn(fieldClass, "mt-1.5")}
              >
                {k !== "fault" ? <option value="">Choose…</option> : null}
                {Object.entries(ENUMS[k]).map(([v, label]) => (
                  <option key={v} value={v}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          ))}
          {(["reported_to_bank_date", "reported_to_ncrp_date"] as const).map((k) => (
            <div key={k}>
              <label htmlFor={`d-${k}`} className="text-sm text-text-2">
                {DETAILS_LABEL[k]} (leave empty if you haven&apos;t yet)
              </label>
              <input
                id={`d-${k}`}
                type="date"
                value={details[k]}
                onChange={(e) => setDetails((s) => ({ ...s, [k]: e.target.value }))}
                className={cn(fieldClass, "mt-1.5")}
              />
            </div>
          ))}
        </div>
      ) : null}

      <FormError message={error ?? c.last_error} />
      <div>
        <PrimaryButton busy={busy} onClick={submit}>
          {needs.size ? "Confirm and assess" : "These are right"}
        </PrimaryButton>
      </div>
    </div>
  );
}
