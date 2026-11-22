import Link from "next/link";
import { Suspense } from "react";

import { AppNav } from "@/components/app/app-nav";

/* Shown while the nav learns the current path (it is only known at request time). */
function NavFallback() {
  return (
    <div className="flex items-center gap-1 sm:gap-2">
      <Link href="/cases" className="rounded-full px-3.5 py-2 text-[0.95rem] text-text-2">
        Cases
      </Link>
      <Link href="/settings" className="rounded-full px-3.5 py-2 text-[0.95rem] text-text-2">
        Settings
      </Link>
    </div>
  );
}

export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <>
      <div className="ambient-app" aria-hidden="true" />
      <div className="relative z-10 flex min-h-screen flex-col">
        <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-5 py-5 sm:px-6">
          <Link
            href="/"
            className="text-[1.0625rem] font-[620] tracking-[-0.02em] [font-stretch:92%]"
          >
            Recourse
          </Link>
          <Suspense fallback={<NavFallback />}>
            <AppNav />
          </Suspense>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-5 pb-20 sm:px-6">{children}</main>
        <footer className="border-t border-line">
          <p className="type-small mx-auto max-w-6xl px-5 py-6 sm:px-6">
            Informational, not legal advice. Not affiliated with RBI or NPCI.
          </p>
        </footer>
      </div>
    </>
  );
}
