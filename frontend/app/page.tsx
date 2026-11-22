import Link from "next/link";

import { Ambient } from "@/components/landing/ambient";
import { CaseStory } from "@/components/landing/case-story";
import { HeroLedger } from "@/components/landing/hero-ledger";
import { ReplyCheck } from "@/components/landing/reply-check";
import { Reveal } from "@/components/landing/reveal";
import { RightsGrid } from "@/components/landing/rights-grid";
import { SiteNav } from "@/components/landing/site-nav";

const START_HREF = "/cases/new";

function Wordmark() {
  return (
    <span className="text-[1.0625rem] font-[620] tracking-[-0.02em] [font-stretch:92%]">
      Recourse
    </span>
  );
}

const COMMITMENTS = [
  {
    title: "Nothing goes out without you",
    body: "Every letter waits for your approval. Recourse never files anything on your behalf.",
  },
  {
    title: "Every number has a source",
    body: "Amounts and dates come from the RBI rule they cite, worked out by fixed rules, not guessed.",
  },
  {
    title: "Your details stay yours",
    body: "Account and card numbers are masked to the last four digits. Codes and passwords are never stored.",
  },
];

export default function Home() {
  return (
    <>
      <Ambient />

      {/* overflow-x: clip (not hidden) keeps the pinned section's sticky working. */}
      <div className="relative z-10 overflow-x-clip">
        <SiteNav startHref={START_HREF} />

        <main>
          {/* Hero ------------------------------------------------------------------------ */}
          <section className="mx-auto grid max-w-6xl items-center gap-14 px-6 pt-12 pb-24 sm:pt-20 lg:grid-cols-[1fr_27rem] lg:gap-16 lg:pt-24 lg:pb-36">
            <div>
              <h1 className="type-display max-w-[13ch]">
                When a payment goes wrong, the rules say what you&apos;re owed.
              </h1>
              <p className="type-lede mt-7 max-w-[42ch]">
                Recourse works out the amount, writes the complaint to your bank and follows it
                through until the money is back. You approve every step.
              </p>
              <div className="mt-10 flex flex-wrap items-center gap-x-6 gap-y-4">
                <Link href={START_HREF} prefetch={false} className="action-primary">
                  Start a case
                </Link>
                <a href="#how" className="text-[0.95rem] text-text-2 transition-colors hover:text-text">
                  See how a case runs
                </a>
              </div>
            </div>
            <div className="flex justify-center lg:justify-end">
              <HeroLedger />
            </div>
          </section>

          {/* What the rules already promise ---------------------------------------------- */}
          <section
            id="rights"
            aria-labelledby="rules-title"
            className="mx-auto max-w-6xl scroll-mt-16 px-6 py-20 sm:py-28"
          >
            <Reveal className="grid gap-5 lg:grid-cols-[1fr_26rem] lg:items-end lg:gap-16">
              <h2 id="rules-title" className="type-title max-w-[17ch]">
                The rules are already on your side.
              </h2>
              <p className="type-lede max-w-[44ch]">
                The Reserve Bank of India sets deadlines for putting failed payments right, and
                makes banks pay you for every day they are late.
              </p>
            </Reveal>

            <RightsGrid />

            <p className="type-small mt-6">
              From RBI circular RBI/2019-20/67 on failed transactions, RBI directions on
              unauthorised electronic banking transactions, and the RBI Integrated Ombudsman
              Scheme, 2026. Conditions apply to each rule.
            </p>
          </section>

          {/* How a case runs (pinned) -------------------------------------------------------- */}
          <div id="how" className="scroll-mt-16">
            <CaseStory />
          </div>

          {/* Trust ------------------------------------------------------------------------- */}
          <section
            id="safety"
            aria-labelledby="trust-title"
            className="relative mx-auto max-w-6xl scroll-mt-16 px-6 py-28 sm:py-40"
          >
            <div
              className="local-glow -top-10 right-[-10%] h-[36rem] w-[36rem]"
              aria-hidden="true"
            />
            <div className="relative grid items-center gap-16 lg:grid-cols-[1fr_30rem] lg:gap-20">
              <Reveal>
                <h2 id="trust-title" className="type-title max-w-[16ch]">
                  It reads every reply, so you don&apos;t have to guess.
                </h2>
                <p className="type-lede mt-5 max-w-[44ch]">
                  When the bank writes back, Recourse checks the reply against the rules. If a
                  message asks for a code or a payment, it tells you plainly and does nothing
                  else.
                </p>
              </Reveal>
              <div className="flex justify-center lg:justify-end">
                <ReplyCheck />
              </div>
            </div>

            <div className="relative mt-24 grid gap-10 border-t border-line pt-12 sm:mt-32 md:grid-cols-3 md:gap-12">
              {COMMITMENTS.map((c, i) => (
                <Reveal key={c.title} delay={i * 0.08}>
                  <h3 className="text-[1.0625rem] font-[600] tracking-[-0.01em] text-text">
                    {c.title}
                  </h3>
                  <p className="mt-2 text-[0.975rem] text-text-2">{c.body}</p>
                </Reveal>
              ))}
            </div>
          </section>

          {/* Close ------------------------------------------------------------------------- */}
          <section className="relative mx-auto max-w-6xl px-6 pt-12 pb-32 sm:pb-44">
            <Reveal className="flex flex-col items-start">
              <h2 className="type-title max-w-[15ch]">Your bank already has rules to follow.</h2>
              <p className="type-lede mt-5 max-w-[40ch]">
                Recourse makes sure they are followed, one calm step at a time.
              </p>
              <Link href={START_HREF} prefetch={false} className="action-primary mt-10">
                Start a case
              </Link>
            </Reveal>
          </section>
        </main>

        <footer className="border-t border-line">
          <div className="mx-auto flex max-w-6xl flex-col gap-3 px-6 py-10 sm:flex-row sm:items-center sm:justify-between">
            <Wordmark />
            <p className="type-small">
              Informational, not legal advice. Not affiliated with RBI or NPCI.
            </p>
          </div>
        </footer>
      </div>
    </>
  );
}
