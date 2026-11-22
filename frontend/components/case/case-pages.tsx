"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { Panel } from "@/components/app/ui";
import { CaseChart } from "@/components/case/case-chart";
import { useCase } from "@/components/case/case-context";
import { ActivityLog, EvidenceList, LettersAndReplies } from "@/components/case/case-sections";
import { NextAction } from "@/components/case/next-action";
import { DatesPanel, OwedPanel } from "@/components/case/owed-panel";
import { nextStep, STEP_TITLE } from "@/lib/case-progress";

/** On pages other than the Overview, a quiet pointer back when the case is waiting on you (phones; desktops have the sidebar). */
function ActionNeeded() {
  const { c } = useCase();
  const step = nextStep(c);
  if (step.owner !== "you") return null;
  return (
    <Link
      href={`/cases/${c.id}`}
      className="mb-5 flex items-center justify-between gap-3 rounded-[1rem] border border-blue/40 bg-blue/[0.07] px-4 py-3 text-sm lg:hidden"
    >
      <span>
        <span className="text-text-3">Your next step: </span>
        <span className="text-text">{STEP_TITLE[step.key]}</span>
      </span>
      <span className="shrink-0 text-blue-bright">Open</span>
    </Link>
  );
}

function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <Panel className="flex flex-col gap-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="type-heading">{title}</h2>
        {aside}
      </div>
      {children}
    </Panel>
  );
}

export function CaseOverview() {
  const { c, refresh } = useCase();
  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_19rem] xl:items-start">
      <NextAction c={c} refresh={refresh} />
      <div className="flex flex-col gap-6">
        <OwedPanel c={c} />
        <DatesPanel c={c} />
      </div>
    </div>
  );
}

export function CaseLetters() {
  const { c } = useCase();
  return (
    <>
      <ActionNeeded />
      <Section title="Letters and replies">
        <LettersAndReplies c={c} />
      </Section>
    </>
  );
}

export function CaseTimeline() {
  const { c } = useCase();
  return (
    <div className="flex flex-col gap-6">
      <ActionNeeded />
      <Section title="Time and deadlines">
        <CaseChart c={c} />
      </Section>
      <Section title="Everything that happened" aside={<span className="text-sm text-text-3">Newest first</span>}>
        <ActivityLog c={c} />
      </Section>
    </div>
  );
}

export function CaseEvidence() {
  const { c } = useCase();
  return (
    <>
      <ActionNeeded />
      <Section title="What Recourse read">
        <EvidenceList c={c} />
      </Section>
    </>
  );
}
