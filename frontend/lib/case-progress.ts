/**
 * Where a case stands, worked out once from the case view and shared by the stage tracker,
 * the Next action frame, the dates panel and the timeline graph. Pure functions, no React.
 */

import type { CaseView, DeadlineLine } from "@/lib/api";

/* ----------------------------------------------------------------------------------------- */
/* Stages                                                                                    */
/* ----------------------------------------------------------------------------------------- */

export const STAGES = [
  { key: "details", label: "Your details" },
  { key: "owed", label: "What you're owed" },
  { key: "letter", label: "Letter to bank" },
  { key: "reply", label: "Bank's reply" },
  { key: "ombudsman", label: "Ombudsman" },
  { key: "outcome", label: "Outcome" },
] as const;

export type StageState = "done" | "current" | "upcoming" | "skipped";

const ENDED = ["RESOLVED", "CLOSED", "ABANDONED"];

function inOmbudsmanStage(c: CaseView): boolean {
  return (
    c.status === "ESCALATION_DRAFT_READY" ||
    c.status === "FILED_WITH_OMBUDSMAN" ||
    c.awaiting_filing ||
    c.can_close ||
    c.needs_bank_type ||
    c.can_recheck_escalation ||
    (c.status === "AWAITING_APPROVAL" && c.draft?.kind === "OMBUDSMAN_COMPLAINT") ||
    (c.status === "JUDGED" && !c.can_escalate) ||
    (c.bank_wait_expired && c.status !== "AWAITING_APPROVAL")
  );
}

/** Index into STAGES of where the case is now. */
export function currentStage(c: CaseView): number {
  if (ENDED.includes(c.status)) return 5;
  if (c.status === "NEW" || c.status === "EXTRACTED") return 0;
  if (inOmbudsmanStage(c)) return 4;
  if (["AWAITING_BANK", "BANK_REPLIED", "JUDGED"].includes(c.status)) return 3;
  if (c.status === "ASSESSED" && c.entitlement && !c.entitlement.covered) return 1;
  return 2;
}

/** For an ended case: which earlier stages it actually went through. */
function reached(c: CaseView, i: number): boolean {
  const has = (t: string) => c.timeline.some((e) => e.event_type === t);
  switch (i) {
    case 0:
      return c.evidence !== null;
    case 1:
      return c.entitlement !== null;
    case 2:
      return c.email?.status === "SENT" || c.bank_complained_on !== null;
    case 3:
      return has("reply.received") || has("bank.wait_expired");
    case 4:
      return c.ombudsman_filed_at !== null || has("case.escalation_chosen");
    default:
      return true;
  }
}

export function stageStates(c: CaseView): StageState[] {
  const now = currentStage(c);
  const ended = ENDED.includes(c.status);
  return STAGES.map((_, i) => {
    if (ended) return i === 5 ? "done" : reached(c, i) ? "done" : "skipped";
    if (i < now) return "done";
    return i === now ? "current" : "upcoming";
  });
}

/* ----------------------------------------------------------------------------------------- */
/* The next step (one decision chain; NextAction renders by its key)                        */
/* ----------------------------------------------------------------------------------------- */

export type Owner = "you" | "recourse" | "bank" | "done";

export type StepKey =
  | "ended"
  | "reading"
  | "review_facts"
  | "bank_type"
  | "recheck_escalation"
  | "redraft"
  | "review_letter"
  | "blocked_send"
  | "gmail_retry"
  | "gmail_sending"
  | "send_yourself"
  | "file_ombudsman"
  | "close"
  | "decision"
  | "preparing_ombudsman"
  | "waiting_bank"
  | "not_covered"
  | "drafting"
  | "starting_wait"
  | "working";

const OWNER: Record<StepKey, Owner> = {
  ended: "done",
  reading: "recourse",
  review_facts: "you",
  bank_type: "you",
  recheck_escalation: "you",
  redraft: "you",
  review_letter: "you",
  blocked_send: "you",
  gmail_retry: "you",
  gmail_sending: "recourse",
  send_yourself: "you",
  file_ombudsman: "you",
  close: "you",
  decision: "you",
  preparing_ombudsman: "recourse",
  waiting_bank: "bank",
  not_covered: "done",
  drafting: "recourse",
  starting_wait: "recourse",
  working: "recourse",
};

export const OWNER_LABEL: Record<Owner, string> = {
  you: "Your turn",
  recourse: "Recourse is working",
  bank: "Waiting for your bank",
  done: "Nothing to do",
};

/** Short names of each step, for the sidebar and the "action needed" link on other pages. */
export const STEP_TITLE: Record<StepKey, string> = {
  ended: "Case ended",
  reading: "Reading your evidence",
  review_facts: "Check the details",
  bank_type: "Say what kind of bank it is",
  recheck_escalation: "Check the Ombudsman complaint again",
  redraft: "Write the letter again",
  review_letter: "Review and approve the letter",
  blocked_send: "Choose your bank",
  gmail_retry: "Send the letter again",
  gmail_sending: "Sending the letter",
  send_yourself: "Send the letter",
  file_ombudsman: "File with the RBI Ombudsman",
  close: "Record how it ended",
  decision: "Decide on the bank's reply",
  preparing_ombudsman: "Preparing the Ombudsman complaint",
  waiting_bank: "Waiting for the bank's reply",
  not_covered: "Not covered by these rules",
  drafting: "Writing your letter",
  starting_wait: "Starting the reply window",
  working: "Working on your case",
};

export function nextStep(c: CaseView): { key: StepKey; owner: Owner } {
  const key = stepKey(c);
  return { key, owner: OWNER[key] };
}

function stepKey(c: CaseView): StepKey {
  const draft = c.draft;
  if (ENDED.includes(c.status)) return "ended";
  if (c.status === "NEW" || (c.status === "EXTRACTED" && !c.evidence)) return "reading";
  if (c.status === "EXTRACTED") return "review_facts";
  if (c.needs_bank_type) return "bank_type";
  if (c.can_recheck_escalation) return "recheck_escalation";
  if (c.can_redraft) return "redraft";
  if (draft?.status === "PENDING" && (c.status === "AWAITING_APPROVAL" || c.status === "ESCALATION_DRAFT_READY")) {
    return "review_letter";
  }
  if (c.can_retry_send && c.email?.status === "BLOCKED") return "blocked_send";
  if (c.can_retry_send && c.email?.channel === "GMAIL") return "gmail_retry";
  if (c.email?.channel === "GMAIL" && c.status === "AWAITING_APPROVAL" && c.email.status !== "SENT") {
    return "gmail_sending";
  }
  if (c.email?.status === "PREPARED" && c.status === "AWAITING_APPROVAL") return "send_yourself";
  if (c.awaiting_filing) return "file_ombudsman";
  if (c.can_close) return "close";
  if (c.status === "JUDGED" && c.can_escalate) return "decision";
  if (c.status === "JUDGED" || c.bank_wait_expired || c.status === "ESCALATION_DRAFT_READY") {
    return "preparing_ombudsman";
  }
  if (c.status === "AWAITING_BANK") return "waiting_bank";
  if (c.status === "ASSESSED" && c.entitlement && !c.entitlement.covered) return "not_covered";
  if (c.status === "ASSESSED") return "drafting";
  if (c.status === "SENT_TO_BANK") return "starting_wait";
  return "working";
}

/* ----------------------------------------------------------------------------------------- */
/* Dates and windows (for the dates panel and the timeline graph)                            */
/* ----------------------------------------------------------------------------------------- */

const DAY = 86_400_000;

/** A calendar date ("2026-11-14" or an ISO timestamp) as a whole day number. */
export function dayNumber(value: string): number {
  const [y, m, d] = value.slice(0, 10).split("-").map(Number);
  return Date.UTC(y, m - 1, d) / DAY;
}

export function dayToIso(day: number): string {
  return new Date(day * DAY).toISOString().slice(0, 10);
}

/**
 * "Today" on the case's own calendar. In demo mode the case's days run fast, so its as-of
 * date is ahead of the real one; otherwise the real date is the later of the two.
 */
export function caseToday(c: CaseView): number {
  const now = new Date();
  const real = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) / DAY;
  const asOf = [c.entitlement?.as_of, c.escalation?.as_of].filter(Boolean) as string[];
  return Math.max(real, ...asOf.map(dayNumber));
}

export function paymentDay(c: CaseView): number | null {
  const v = c.evidence?.facts.txn_timestamp?.value;
  return typeof v === "string" && v ? dayNumber(v) : null;
}

export function complaintDay(c: CaseView): number | null {
  const v = c.bank_complained_on ?? c.email?.sent_at ?? c.details.reported_to_bank_date;
  return v ? dayNumber(v) : null;
}

/** Plain names for each window the rule engine returns (DeadlineLine.key). */
const WINDOW_NAME: Record<string, string> = {
  reversal_deadline: "Bank's time to reverse",
  report_for_zero_liability: "Report for zero liability",
  report_for_limited_liability: "Report for capped liability",
  report_for_small_value_compensation: "Report for compensation",
  bank_resolution: "Bank's time to resolve",
  shadow_reversal: "Provisional credit due",
  ombudsman_eligible_from: "Waiting for the bank",
  ombudsman_filing_deadline: "Ombudsman filing window",
};

export type Window = {
  key: string;
  name: string;
  line: DeadlineLine;
  start: number;
  end: number;
};

/** Every deadline as a window with a start: from the payment, the complaint, or the end of the bank's wait. */
export function windows(c: CaseView): Window[] {
  const paid = paymentDay(c);
  const complained = complaintDay(c);
  const lines = [...(c.entitlement?.deadlines ?? []), ...(c.escalation?.deadlines ?? [])];
  const eligible = lines.find((d) => d.key === "ombudsman_eligible_from");
  const out: Window[] = [];
  for (const line of lines) {
    const end = dayNumber(line.due);
    let start: number | null;
    switch (line.key) {
      case "bank_resolution":
      case "shadow_reversal":
      case "ombudsman_eligible_from":
        start = complained ?? paid;
        break;
      case "ombudsman_filing_deadline":
        start = eligible ? dayNumber(eligible.due) : complained;
        break;
      default:
        start = paid;
    }
    out.push({
      key: line.key,
      name: WINDOW_NAME[line.key] ?? line.label,
      line,
      start: Math.min(start ?? end, end),
      end,
    });
  }
  return out;
}

/** How much of a window has gone by on `today` (0 to 1). */
export function elapsed(w: Window, today: number): number {
  if (w.end <= w.start) return today >= w.end ? 1 : 0;
  return Math.min(1, Math.max(0, (today - w.start) / (w.end - w.start)));
}
