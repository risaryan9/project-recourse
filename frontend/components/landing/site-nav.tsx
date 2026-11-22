"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

const SECTIONS = [
  { href: "#rights", label: "Your rights" },
  { href: "#how", label: "How it works" },
  { href: "#safety", label: "Safety" },
];

/**
 * The landing page's top bar. It sits on the ink at first and gains a quiet painted
 * background once the page scrolls (no backdrop-filter, see DESIGN.md "Glass").
 */
export function SiteNav({ startHref }: { startHref: string }) {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={cn(
        "sticky top-0 z-40 border-b transition-[background-color,border-color] duration-500 ease-[var(--ease-calm)]",
        scrolled ? "border-line bg-[rgb(5_5_7/0.86)]" : "border-transparent bg-transparent",
      )}
    >
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-6 px-6">
        <Link href="/" aria-label="Recourse home" className="flex items-center gap-2.5">
          <span
            aria-hidden="true"
            className="grid size-7 place-items-center rounded-[0.55rem] bg-blue text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.25),0_6px_20px_-6px_rgb(59_130_246/0.7)]"
          >
            <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 14 4 9l5-5" />
              <path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />
            </svg>
          </span>
          <span className="text-[1.0625rem] font-[620] tracking-[-0.02em] [font-stretch:92%]">
            Recourse
          </span>
        </Link>

        <nav aria-label="Page sections" className="hidden md:block">
          <ul className="flex items-center gap-1 rounded-full border border-line bg-white/[0.025] p-1">
            {SECTIONS.map((s) => (
              <li key={s.href}>
                <a
                  href={s.href}
                  className="block rounded-full px-3.5 py-1.5 text-[0.9rem] text-text-2 transition-colors hover:bg-white/[0.05] hover:text-text"
                >
                  {s.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="flex items-center gap-2 sm:gap-3">
          <Link
            href="/cases"
            prefetch={false}
            className="hidden px-2 text-[0.9rem] text-text-2 transition-colors hover:text-text sm:inline"
          >
            My cases
          </Link>
          <Link href={startHref} prefetch={false} className="action-quiet">
            Start a case
          </Link>
        </div>
      </div>
    </header>
  );
}
