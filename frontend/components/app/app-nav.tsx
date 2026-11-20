"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useConfig, useGmail } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/cases", label: "Cases" },
  { href: "/settings", label: "Settings" },
];

export function AppNav() {
  const path = usePathname();
  const config = useConfig();
  const { status: gmail } = useGmail();
  return (
    <div className="flex items-center gap-1 sm:gap-2">
      {gmail?.configured ? (
        <Link
          href="/settings"
          className="mr-1 hidden items-center gap-2 rounded-full border border-line px-3 py-1 text-sm text-text-2 hover:text-text sm:inline-flex"
          title={gmail.connected ? `Sending from ${gmail.email}` : "Connect Gmail in Settings"}
        >
          <span aria-hidden="true" className={cn("size-1.5 rounded-full", gmail.connected ? "bg-blue" : "bg-text-3")} />
          {gmail.connected ? "Gmail connected" : "Gmail not connected"}
        </Link>
      ) : null}
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
