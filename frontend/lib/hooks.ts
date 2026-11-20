"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError, type GmailStatus, type PublicConfig } from "@/lib/api";

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
};

/**
 * Load `fetcher` and keep it fresh every `intervalMs` while `active(data)` says so. Pauses
 * while the tab is hidden. `refresh()` reloads at once (after the user acts).
 */
export function usePoll<T>(
  fetcher: () => Promise<T>,
  { intervalMs = 2500, active = () => true }: { intervalMs?: number; active?: (d: T) => boolean } = {},
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

  return { data, error, loading, refresh };
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
