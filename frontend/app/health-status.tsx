"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Health = {
  api: "ok";
  temporal: {
    reachable: boolean;
    address: string;
    namespace: string;
    error: string | null;
  };
};

type State =
  | { kind: "loading" }
  | { kind: "ok"; health: Health; checkedAt: Date }
  | { kind: "error"; message: string; checkedAt: Date };

function Badge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`rounded-full px-3 py-1 text-sm font-medium ${
        ok
          ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
          : "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
      }`}
    >
      {label}
    </span>
  );
}

function Row({ name, children }: { name: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-3">
      <span className="text-zinc-600 dark:text-zinc-400">{name}</span>
      {children}
    </div>
  );
}

async function fetchHealth(): Promise<State> {
  try {
    const res = await fetch(`${API_URL}/health`, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return { kind: "ok", health: (await res.json()) as Health, checkedAt: new Date() };
  } catch (err) {
    return {
      kind: "error",
      message: err instanceof Error ? err.message : String(err),
      checkedAt: new Date(),
    };
  }
}

export default function HealthStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchHealth().then((next) => {
      if (!cancelled) setState(next);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = () => {
    setState({ kind: "loading" });
    fetchHealth().then(setState);
  };

  return (
    <section className="w-full max-w-md rounded-xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
      <h2 className="mb-2 text-lg font-semibold">System health</h2>
      <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
        <Row name="API">
          {state.kind === "loading" ? (
            <span className="text-sm text-zinc-500">Checking…</span>
          ) : (
            <Badge ok={state.kind === "ok"} label={state.kind === "ok" ? "OK" : "Unreachable"} />
          )}
        </Row>
        <Row name="Temporal">
          {state.kind === "ok" ? (
            <Badge
              ok={state.health.temporal.reachable}
              label={state.health.temporal.reachable ? "Reachable" : "Unreachable"}
            />
          ) : (
            <span className="text-sm text-zinc-500">{state.kind === "loading" ? "Checking…" : "—"}</span>
          )}
        </Row>
      </div>
      <p className="mt-4 text-xs text-zinc-500" data-testid="health-detail">
        {state.kind === "ok" &&
          `Temporal ${state.health.temporal.address} (${state.health.temporal.namespace})` +
            (state.health.temporal.error ? ` — ${state.health.temporal.error}` : "")}
        {state.kind === "error" && `Could not reach ${API_URL}: ${state.message}`}
        {state.kind !== "loading" && ` · checked ${state.checkedAt.toLocaleTimeString()}`}
      </p>
      <button
        onClick={refresh}
        disabled={state.kind === "loading"}
        className="mt-4 rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
      >
        Refresh
      </button>
    </section>
  );
}
