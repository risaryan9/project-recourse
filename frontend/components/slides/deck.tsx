"use client";

import { useEffect, useState, type ReactNode } from "react";

/**
 * Full-screen 16:9 deck: each slide is drawn at 1920x1080 and scaled to fit the window.
 * Arrow keys (and space / page keys) move between slides; the URL hash (#1, #2, #3) keeps
 * the place, so a slide can be linked or captured directly.
 */
export function Deck({ slides }: { slides: ReactNode[] }) {
  const [i, setI] = useState(0);
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const fromHash = () => {
      const n = Number(window.location.hash.slice(1));
      if (Number.isInteger(n) && n >= 1 && n <= slides.length) setI(n - 1);
    };
    const fit = () => setScale(Math.min(window.innerWidth / 1920, window.innerHeight / 1080));
    fromHash();
    fit();
    window.addEventListener("hashchange", fromHash);
    window.addEventListener("resize", fit);
    return () => {
      window.removeEventListener("hashchange", fromHash);
      window.removeEventListener("resize", fit);
    };
  }, [slides.length]);

  useEffect(() => {
    const go = (n: number) => {
      const next = Math.max(0, Math.min(slides.length - 1, n));
      setI(next);
      history.replaceState(null, "", `#${next + 1}`);
    };
    const onKey = (e: KeyboardEvent) => {
      if (["ArrowRight", "ArrowDown", "PageDown", " "].includes(e.key)) {
        e.preventDefault();
        go(i + 1);
      } else if (["ArrowLeft", "ArrowUp", "PageUp"].includes(e.key)) {
        e.preventDefault();
        go(i - 1);
      } else if (e.key === "Home") go(0);
      else if (e.key === "End") go(slides.length - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [i, slides.length]);

  return (
    <main className="fixed inset-0 grid place-items-center overflow-hidden bg-ink">
      <div
        className="origin-center"
        style={{ width: 1920, height: 1080, transform: `scale(${scale})`, flexShrink: 0 }}
        aria-roledescription="slide"
        aria-label={`Slide ${i + 1} of ${slides.length}`}
      >
        {slides[i]}
      </div>
    </main>
  );
}
