/**
 * Typed client for the Recourse API (backend/recourse/api). Every request sends the
 * anonymous owner cookie (`credentials: "include"`), so a browser sees only its own cases.
 */

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/* ----------------------------------------------------------------------------------------- */
/* Types (mirror the backend's response models)                                              */
/* ----------------------------------------------------------------------------------------- */

export type CaseStatus =
  | "NEW"
  | "EXTRACTED"
  | "ASSESSED"
  | "DRAFT_READY"
  | "AWAITING_APPROVAL"
  | "SENT_TO_BANK"
  | "AWAITING_BANK"
  | "BANK_REPLIED"
  | "JUDGED"
  | "RESOLVED"
  | "ESCALATION_DRAFT_READY"
  | "FILED_WITH_OMBUDSMAN"
  | "CLOSED"
  | "ABANDONED";

export const ENDED: CaseStatus[] = ["RESOLVED", "CLOSED", "ABANDONED"];

export type PublicConfig = { email_sandbox: boolean; demo_mode: boolean; demo_day_seconds: number };

export type GmailStatus = { configured: boolean; connected: boolean; email: string | null };

/** Google sign-in is a full-page navigation to the API, not a fetch. Behind this site's own
 * proxy (API_URL "/api"), sign-in stays at /auth/* so its cookie path matches (proxy.ts). */
export const GMAIL_CONNECT_URL = API_URL.startsWith("/") ? "/auth/google/start" : `${API_URL}/auth/google/start`;

export type CaseSummary = {
  id: string;
  case_tag: string;
  status: CaseStatus;
  created_at: string;
  updated_at: string;
  bank_name: string | null;
  amount_inr: string | null;
  total_owed_inr: string | null;
  owed_as_of: string | null;
  next_deadline: { key: string; label: string; due: string } | null;
};

export type Fact = {
  value: unknown;
  confidence: number;
  source_text: string | null;
  origin: string;
};

export type FieldIssue = {
  field: string;
  code: string;
  severity: "ERROR" | "WARNING" | "INFO";
  message: string;
};

export type Citation = {
  doc_id: string;
  doc_title: string;
  doc_ref: string;
  para: string;
  url: string;
  quotes: string[];
  verified: boolean;
};

export type MoneyLine = {
  key: string;
  label: string;
  amount_inr: string;
  accruing: boolean;
  citation: Citation;
};

export type DeadlineLine = {
  key: string;
  label: string;
  due: string;
  passed: boolean;
  approximate: boolean;
  citation: Citation;
};

export type Flag = { code: string; citation: Citation | null };

export type Entitlement = {
  case_type: "failed_transaction" | "fraud";
  as_of: string;
  covered: boolean;
  not_covered_reason: string | null;
  regime_id: string | null;
  rule_id: string | null;
  money: MoneyLine[];
  deadlines: DeadlineLine[];
  flags: Flag[];
  citations: Citation[];
};

export type Escalation = {
  scheme_id: string;
  as_of: string;
  entity_coverage: "COVERED" | "CONDITIONAL" | "NOT_COVERED" | "UNKNOWN";
  can_file_now: boolean;
  eligible_from: string | null;
  waiting_period_ends: string | null;
  filing_deadline: string | null;
  deadlines: DeadlineLine[];
  flags: Flag[];
  citations: Citation[];
};

export type CaseDetails = {
  bank_type: string | null;
  account_type: string | null;
  fault: string;
  reported_to_bank_date: string | null;
  reported_to_ncrp_date: string | null;
};

export type Draft = {
  id: string;
  seq: number;
  kind: "BANK_COMPLAINT" | "OMBUDSMAN_COMPLAINT";
  status: "PENDING" | "APPROVED" | "REJECTED" | "FAILED";
  subject: string | null;
  body: string | null;
  user_fill: string[];
  rules_verified: boolean;
  disclaimer: string;
  final_subject: string | null;
  final_body: string | null;
  approved_at: string | null;
  created_at: string;
};

export type Bank = {
  id: string;
  name: string;
  has_email: boolean;
  complaint_form_url: string | null;
  verified: boolean;
};

export type Email = {
  id: string;
  channel: "MANUAL" | "GMAIL";
  status: "BLOCKED" | "PREPARED" | "SENDING" | "SENT";
  sandbox: boolean;
  bank: Bank | null;
  to: string | null;
  subject: string;
  body: string;
  mailto: string | null;
  blocked_reasons: string[];
  sent_at: string | null;
  thread_id: string | null;
};

export type ReplySummary = {
  reply_id: string;
  label: string;
  next_action:
    | "WARN"
    | "KEEP_WAITING"
    | "USER_ACTION"
    | "PROPOSE_RESOLVED"
    | "ESCALATE"
    | "ASK_USER";
  suspicious: boolean;
  warnings: string[];
  complaint_number: string | null;
};

export type TimelineEntry = {
  occurred_at: string;
  actor: "USER" | "SYSTEM" | "AGENT" | "BANK";
  event_type: string;
  details: Record<string, unknown>;
};

export type CaseView = {
  id: string;
  case_tag: string;
  status: CaseStatus;
  created_at: string;
  updated_at: string;
  evidence: {
    source_kind: string;
    extracted_by: string;
    facts: Record<string, Fact>;
    issues: FieldIssue[];
    needs_confirmation: string[];
    redactions: string[];
    notes: string[];
    txn_time_known: boolean;
  } | null;
  details: CaseDetails;
  missing_details: string[];
  entitlement: Entitlement | null;
  last_error: string | null;
  draft: Draft | null;
  can_redraft: boolean;
  email: Email | null;
  can_retry_send: boolean;
  bank: Bank | null;
  bank_complained_on: string | null;
  escalation: Escalation | null;
  bank_wait_expired: boolean;
  bank_complaint_ref: string | null;
  last_reply: ReplySummary | null;
  can_escalate: boolean;
  needs_bank_type: boolean;
  can_recheck_escalation: boolean;
  awaiting_filing: boolean;
  filing_deadline_passed: boolean;
  can_close: boolean;
  ombudsman_filed_at: string | null;
  ombudsman_ref: string | null;
  watching_thread: boolean;
  can_resolve: boolean;
  last_thread_check: string | null;
  timeline: TimelineEntry[];
};

export type Reply = {
  id: string;
  source: "PASTE" | "GMAIL";
  received_at: string;
  sender: string | null;
  text: string;
  status: "PENDING" | "JUDGED";
  label: string | null;
  next_action: string | null;
  suspicious: boolean | null;
  verdict: Record<string, unknown> | null;
};

export type Filing = {
  portal_url: string;
  other_ways: string[];
  fields: { label: string; value: string }[];
  title: string;
  complaint_text: string;
  filing_deadline: string | null;
  filing_deadline_passed: boolean;
  disclaimer: string;
};

/* ----------------------------------------------------------------------------------------- */
/* Requests                                                                                  */
/* ----------------------------------------------------------------------------------------- */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

/** A readable sentence from FastAPI's error bodies (string, {message, errors} or a list). */
function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    const d = detail as { message: string; errors?: string[]; fields?: string[] };
    const extra = d.errors ?? d.fields;
    return extra?.length ? `${d.message} ${extra.join("; ")}` : d.message;
  }
  if (Array.isArray(detail)) {
    return detail
      .map((e: { loc?: unknown[]; msg?: string }) =>
        [e.loc?.slice(-1)[0], e.msg].filter(Boolean).join(": "),
      )
      .join("; ");
  }
  return "Something went wrong.";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      credentials: "include",
      cache: "no-store",
      ...init,
      headers:
        init.body && !(init.body instanceof FormData)
          ? { "content-type": "application/json", ...init.headers }
          : init.headers,
    });
  } catch {
    throw new ApiError(0, "Recourse can't be reached right now. Check your connection and try again.");
  }
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = (await res.json()).detail;
    } catch {
      detail = undefined;
    }
    throw new ApiError(res.status, describe(detail), detail);
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });

export const api = {
  config: () => request<PublicConfig>("/config"),
  gmailStatus: () => request<GmailStatus>("/auth/google/status"),
  gmailDisconnect: () => request<GmailStatus>("/auth/google/disconnect", { method: "POST" }),
  listCases: () => request<CaseSummary[]>("/cases"),
  createCase: (form: FormData) =>
    request<{ id: string; case_tag: string; status: CaseStatus }>("/cases", {
      method: "POST",
      body: form,
    }),
  getCase: (id: string) => request<CaseView>(`/cases/${id}`),
  deleteMyCases: () => request<{ deleted: number }>("/cases", { method: "DELETE" }),
  confirm: (id: string, body: { corrections: Record<string, unknown>; details?: Partial<CaseDetails> }) =>
    post(`/cases/${id}/confirm`, body),
  abandon: (id: string, reason?: string) =>
    post(`/cases/${id}/abandon`, reason ? { reason } : undefined),
  approveDraft: (id: string, draftId: string, body: { customer_name: string; subject?: string; body?: string }) =>
    post<Draft>(`/cases/${id}/drafts/${draftId}/approve`, body),
  rejectDraft: (id: string, draftId: string, reason: string) =>
    post(`/cases/${id}/drafts/${draftId}/reject`, { reason }),
  redraft: (id: string) => post(`/cases/${id}/redraft`),
  banks: () => request<Bank[]>("/banks"),
  chooseBank: (id: string, bankId: string) => put<Bank>(`/cases/${id}/bank`, { bank_id: bankId }),
  markSent: (id: string) => post(`/cases/${id}/email/mark-sent`),
  retrySend: (id: string) => post(`/cases/${id}/email/retry`),
  addReply: (id: string, body: { text: string; sender?: string }) =>
    post<{ id: string; redactions: string[] }>(`/cases/${id}/bank-reply`, body),
  replies: (id: string) => request<Reply[]>(`/cases/${id}/replies`),
  resolved: (id: string, amount: string) =>
    post(`/cases/${id}/resolved`, { amount_received_inr: amount }),
  escalate: (id: string) => post(`/cases/${id}/escalate`),
  setBankType: (id: string, bankType: string) =>
    put(`/cases/${id}/bank-type`, { bank_type: bankType }),
  recheckEscalation: (id: string) => post(`/cases/${id}/escalation/recheck`),
  filing: (id: string) => request<Filing>(`/cases/${id}/ombudsman-filing`),
  markFiled: (id: string, reference?: string) =>
    post(`/cases/${id}/ombudsman/mark-filed`, reference ? { reference } : {}),
  close: (id: string, outcome: string, amount?: string) =>
    post(`/cases/${id}/close`, { outcome, amount_received_inr: amount || null }),
};
