/**
 * Bring your own key (ADR-023): the visitor's AI keys, kept only in this browser tab
 * (sessionStorage, cleared when the tab closes). They are sent to the API once, which checks
 * them and holds them in memory; if the API restarts, `resendKeys` sends them again.
 */

export type LocalKeys = { anthropic_key?: string; openai_key?: string };

const SLOT = "recourse.keys";

export function readKeys(): LocalKeys | null {
  try {
    const raw = sessionStorage.getItem(SLOT);
    if (!raw) return null;
    const k = JSON.parse(raw) as LocalKeys;
    return k.anthropic_key || k.openai_key ? k : null;
  } catch {
    return null;
  }
}

export function writeKeys(k: LocalKeys): void {
  try {
    sessionStorage.setItem(SLOT, JSON.stringify(k));
  } catch {
    /* private mode: the key still works until the server forgets it */
  }
}

export function clearKeys(): void {
  try {
    sessionStorage.removeItem(SLOT);
  } catch {
    /* nothing stored */
  }
}
