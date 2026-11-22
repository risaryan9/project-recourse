import type { ReactNode } from "react";

/**
 * One 1920x1080 slide on the site's own ink: the blue light, the lit grid and the grain from
 * the landing page (DESIGN.md "Light and colour"), drawn inside the slide so it scales with it.
 */
export function SlideFrame({ children }: { children: ReactNode }) {
  return (
    <div className="relative h-[1080px] w-[1920px] overflow-hidden bg-ink text-text">
      <div aria-hidden="true" className="absolute inset-0">
        <div
          className="absolute -top-[520px] -right-[320px] size-[1400px] rounded-full"
          style={{ background: "radial-gradient(closest-side, rgb(59 130 246 / 0.15), transparent 72%)" }}
        />
        <div
          className="absolute -bottom-[640px] -left-[420px] size-[1200px] rounded-full"
          style={{ background: "radial-gradient(closest-side, rgb(59 130 246 / 0.09), transparent 72%)" }}
        />
        <div className="ambient-grid" />
        <div className="ambient-grain" />
      </div>
      <div className="relative h-full w-full">{children}</div>
    </div>
  );
}

/** A technology's logo on a small glass tile (official mark, white monochrome). */
export function LogoTile({ src, name, size = 44 }: { src: string; name: string; size?: number }) {
  return (
    <span
      className="glass grid shrink-0 place-items-center rounded-[0.85rem]"
      style={{ width: size, height: size }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- static SVG marks */}
      <img src={src} alt={name} width={size * 0.5} height={size * 0.5} className="block" />
    </span>
  );
}
