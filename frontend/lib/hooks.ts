"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError, type GmailStatus, type KeyStatus, type PublicConfig } from "@/lib/api";
import { readKeys } from "@/lib/keys";

let configPromise: Promise<PublicConfig> | null = null;

/** The API's public flags (sandbox, demo mode), fetched once per page load. */
export function useConfig(): PublicConfig | null {
  const [config, setConfig] = useState<PublicConfig | null>(null);
  useEffect(() => {
    configPromise ??= api.config().catch((e) => {
      configPromise = null;
      throw e;
    });
    configPromise.then(setConfig, () => setConfig(null));
  }, []);
  return config;
}

type Poll<T> = {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  refresh: () => Promise<void>;
  /** After an action: reload quickly until the data changes (or ~12 s pass). Needs `changeKey`. */
  settle: () => Promise<void>;
};

/**
 * Load `fetcher` and keep it fresh every `intervalMs` while `active(data)` says so. Pauses
 * while the tab is hidden. `refresh()` reloads at once (after the user acts).
 */
export function usePoll<T>(
  fetcher: () => Promise<T>,
  {
    intervalMs = 2500,
    active = () => true,
    changeKey,
  }: { intervalMs?: number; active?: (d: T) => boolean; changeKey?: (d: T) => string } = {},
): Poll<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const fetchRef = useRef(fetcher);
  const activeRef = useRef(active);
  useEffect(() => {
    fetchRef.current = fetcher;
    activeRef.current = active;
  });

  const latest = useRef<T | null>(null);
  const refresh = useCallback(async () => {
    try {
      const next = await fetchRef.current();
      latest.current = next;
      setData(next);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const loop = async () => {
      if (stopped) return;
      if (document.visibilityState === "visible") await refresh();
      // Stop once nothing more will change by itself (`refresh()` still works after that).
      if (stopped || (latest.current !== null && !activeRef.current(latest.current))) return;
      timer = setTimeout(loop, intervalMs);
    };
    loop();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [refresh, intervalMs]);

  // Actions are accepted at once (202) and applied by the case workflow a moment later, so a
  // single reload right after one often still sees the old state. Check every 500 ms until
  // something changes, so the screen moves as soon as the step is recorded.
  const keyRef = useRef(changeKey);
  useEffect(() => {
    keyRef.current = changeKey;
  });
  const settle = useCallback(async () => {
    const key = keyRef.current;
    const before = latest.current !== null && key ? key(latest.current) : null;
    await refresh();
    if (before === null || !key) return;
    const until = Date.now() + 12_000;
    while (latest.current !== null && key(latest.current) === before && Date.now() < until) {
      await new Promise((r) => setTimeout(r, 500));
      await refresh();
    }
  }, [refresh]);

  return { data, error, loading, refresh, settle };
}

/** Whether this browser has connected Gmail. `refresh()` after connecting or disconnecting. */
export function useGmail(): { status: GmailStatus | null; refresh: () => void } {
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const refresh = useCallback(() => {
    api.gmailStatus().then(setStatus, () => setStatus(null));
  }, []);
  useEffect(refresh, [refresh]);
  return { status, refresh };
}

/**
 * Bring your own key (ADR-023): whether the API holds this browser's key. If it doesn't but
 * this tab has one (the API restarted, or its memory copy expired), send it again quietly.
 */
export function useKeyStatus(): { status: KeyStatus | null; refresh: () => void } {
  const [status, setStatus] = useState<KeyStatus | null>(null);
  const refresh = useCallback(() => {
    api.keyStatus().then(
      async (s) => {
        const local = readKeys();
        if (s.required && !s.anthropic && !s.openai && local) {
          try {
            s = await api.saveKeys(local);
          } catch {
            /* the key no longer works: show the form */
          }
        }
        setStatus(s);
      },
      () => setStatus(null),
    );
  }, []);
  useEffect(refresh, [refresh]);
  return { status, refresh };
}
