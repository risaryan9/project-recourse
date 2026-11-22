"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { CaseView } from "@/lib/api";

/** The case, loaded once by the case layout and shared by its pages (one poll, not four). */
type CaseCtx = { c: CaseView; refresh: () => void | Promise<void> };

const Ctx = createContext<CaseCtx | null>(null);

export function CaseProvider({ value, children }: { value: CaseCtx; children: ReactNode }) {
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCase(): CaseCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useCase() outside the case layout");
  return v;
}
