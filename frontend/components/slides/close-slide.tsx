import { SlideFrame } from "@/components/slides/frame";

const LINKS = [
  { label: "Code", value: "github.com/risaryan9/project-recourse" },
  { label: "Live", value: "recourse-omega.vercel.app" },
  { label: "GitHub", value: "github.com/risaryan9" },
  { label: "LinkedIn", value: "linkedin.com/in/aryanr19" },
];

export function CloseSlide() {
  return (
    <SlideFrame>
      <div className="flex h-full flex-col justify-between px-[120px] pt-[150px] pb-[96px]">
        <div>
          <div className="flex items-center gap-5">
            <span
              aria-hidden="true"
              className="grid size-[72px] place-items-center rounded-[1.25rem] bg-blue text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.25),0_18px_60px_-12px_rgb(59_130_246/0.7)]"
            >
              <svg viewBox="0 0 24 24" className="size-10" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 14 4 9l5-5" />
                <path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />
              </svg>
            </span>
            <h2 className="text-[132px] leading-none font-[620] tracking-[-0.045em] [font-stretch:88%]">Recourse</h2>
          </div>
          <p className="mt-10 max-w-[30ch] text-[44px] leading-[1.15] font-[520] tracking-[-0.02em] text-text-2 [font-stretch:92%] [text-wrap:balance]">
            Rights people already have, turned into money they actually get back.
          </p>
        </div>

        <div className="flex items-end justify-between gap-16">
          <div className="glass glass-lit grid grid-cols-2 gap-x-16 gap-y-6 rounded-[1.75rem] px-10 py-8">
            {LINKS.map((l) => (
              <div key={l.label}>
                <div className="text-[16px] text-text-3">{l.label}</div>
                <div className="mt-1 text-[24px] tracking-[-0.01em] text-text">{l.value}</div>
              </div>
            ))}
          </div>
          <div className="text-right">
            <div className="text-[26px] text-text">Built for Arthaton 2026</div>
            <div className="mt-1 text-[20px] text-blue-bright">arthaton.com</div>
          </div>
        </div>
      </div>
    </SlideFrame>
  );
}
