"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useConfig } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/cases", label: "Cases" },
  { href: "/settings", label: "Settings" },
];

export function AppNav() {
  const path = usePathname();
  const config = useConfig();
  return (
    <div className="flex items-center gap-1 sm:gap-2">
      {config?.email_sandbox ? (
        <span
          className="mr-2 hidden rounded-full border border-line px-3 py-1 text-sm text-text-2 sm:inline"
          title="Emails go to a test inbox, never to a real bank."
        >
          Sandbox
        </span>
      ) : null}
      {LINKS.map((l) => {
        const on = path === l.href || (l.href === "/cases" && path.startsWith("/cases"));
        return (
          <Link
            key={l.href}
            href={l.href}
            aria-current={on ? "page" : undefined}
            className={cn(
              "rounded-full px-3.5 py-2 text-[0.95rem] transition-colors",
              on ? "bg-white/[0.06] text-text" : "text-text-2 hover:text-text",
            )}
          >
            {l.label}
          </Link>
        );
      })}
    </div>
  );
}
