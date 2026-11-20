/** Plain-language names for what the backend calls things (DESIGN.md: name things as people know them). */

import type { CaseStatus, TimelineEntry } from "@/lib/api";

export const STATUS: Record<CaseStatus, { label: string; live: boolean }> = {
  NEW: { label: "Reading your evidence", live: true },
  EXTRACTED: { label: "Check the details", live: false },
  ASSESSED: { label: "Writing your letter", live: true },
  DRAFT_READY: { label: "Letter ready", live: false },
  AWAITING_APPROVAL: { label: "Waiting for you", live: false },
  SENT_TO_BANK: { label: "Sent to the bank", live: true },
  AWAITING_BANK: { label: "Waiting for the bank", live: true },
  BANK_REPLIED: { label: "Bank replied", live: true },
  JUDGED: { label: "Your decision", live: false },
  RESOLVED: { label: "Resolved", live: false },
  ESCALATION_DRAFT_READY: { label: "Ombudsman complaint", live: false },
  FILED_WITH_OMBUDSMAN: { label: "Filed with the Ombudsman", live: true },
  CLOSED: { label: "Closed", live: false },
  ABANDONED: { label: "Stopped", live: false },
};

/** Evidence facts, in the order the review form shows them. */
export const FACTS: { name: string; label: string; kind: "text" | "money" | "datetime" | "enum" | "bool" }[] = [
  { name: "case_type", label: "What happened", kind: "enum" },
  { name: "scenario", label: "What went wrong", kind: "enum" },
  { name: "channel", label: "Payment type", kind: "enum" },
  { name: "amount_inr", label: "Amount", kind: "money" },
  { name: "txn_timestamp", label: "Date and time", kind: "datetime" },
  { name: "account_debited", label: "Was your account debited?", kind: "bool" },
  { name: "utr", label: "Transaction reference (UTR / RRN)", kind: "text" },
  { name: "other_reference", label: "Other reference", kind: "text" },
  { name: "payer_bank", label: "Your bank", kind: "text" },
  { name: "payer_account_last4", label: "Your account (last 4 digits)", kind: "text" },
  { name: "card_last4", label: "Card (last 4 digits)", kind: "text" },
  { name: "payment_app", label: "Paid from (app)", kind: "text" },
  { name: "payee_name", label: "Paid to", kind: "text" },
  { name: "payee_vpa", label: "Their UPI ID", kind: "text" },
  { name: "payee_bank", label: "Their bank", kind: "text" },
  { name: "atm_or_terminal_id", label: "ATM or terminal ID", kind: "text" },
  { name: "reversal_amount_inr", label: "Amount returned", kind: "money" },
  { name: "reversal_timestamp", label: "Money returned on", kind: "datetime" },
  { name: "alert_received_at", label: "Bank alert received", kind: "datetime" },
  { name: "txn_status", label: "Status shown", kind: "enum" },
  { name: "is_domestic", label: "Within India", kind: "bool" },
];

export const ENUMS: Record<string, Record<string, string>> = {
  case_type: { failed_transaction: "A failed payment", fraud: "A payment I didn't make" },
  scenario: {
    BENEFICIARY_NOT_CREDITED: "Debited, but the other person wasn't credited",
    CASH_NOT_DISPENSED: "Debited, but the ATM gave no cash",
    MERCHANT_NOT_CONFIRMED: "Debited, but the merchant didn't confirm",
    MANDATE_REVOKED_DEBIT: "Debited after I cancelled the mandate",
    CUSTOMER_ERROR: "Sent to the wrong person by mistake",
  },
  channel: {
    UPI: "UPI",
    IMPS: "IMPS",
    ATM: "ATM",
    CARD_TO_CARD: "Card to card",
    CARD_POS: "Card at a shop",
    CARD_NOT_PRESENT: "Card online",
    AEPS: "AePS",
    APBS: "APBS",
    NACH: "NACH",
    PPI_ON_US: "Wallet",
  },
  txn_status: { FAILED: "Failed", PENDING: "Pending", SUCCESS: "Successful", REVERSED: "Reversed" },
  bank_type: {
    COMMERCIAL_BANK: "A commercial bank (public, private or foreign)",
    SMALL_FINANCE_BANK: "A small finance bank",
    PAYMENTS_BANK: "A payments bank",
    REGIONAL_RURAL_BANK: "A regional rural bank",
    LOCAL_AREA_BANK: "A local area bank",
    COOPERATIVE_BANK: "A co-operative bank",
    NBFC: "A non-bank lender (NBFC)",
    NON_BANK_PPI_ISSUER: "A wallet company (non-bank)",
  },
  account_type: {
    SAVINGS: "Savings account",
    BSBD: "Basic savings (BSBD) account",
    INDIVIDUAL_CURRENT_CC_OD_UP_TO_25_LAKH: "Current / overdraft, limit up to ₹25 lakh",
    OTHER_CURRENT_CC_OD: "Other current / overdraft",
    MSME_CURRENT_CC_OD: "Business (MSME) current account",
    CREDIT_CARD_UP_TO_5_LAKH: "Credit card, limit up to ₹5 lakh",
    CREDIT_CARD_ABOVE_5_LAKH: "Credit card, limit above ₹5 lakh",
    PPI_OR_GIFT_CARD: "Wallet or gift card",
  },
  fault: {
    UNKNOWN: "Not sure yet",
    THIRD_PARTY: "Neither me nor the bank (someone else)",
    BANK: "The bank",
    CUSTOMER: "I may have shared a code or password",
  },
  outcome: {
    AWARD_RECEIVED: "The Ombudsman's award was paid",
    SETTLED_BY_BANK: "The bank paid after I filed",
    REJECTED_BY_OMBUDSMAN: "The Ombudsman rejected it",
    WITHDRAWN: "I withdrew it",
  },
};

export const DETAILS_LABEL: Record<string, string> = {
  bank_type: "What kind of bank is it?",
  account_type: "Which account was it?",
  fault: "Whose fault was it?",
  reported_to_bank_date: "When did you report it to the bank?",
  reported_to_ncrp_date: "When did you report it on 1930 or cybercrime.gov.in?",
};

/** One sentence per audit event, for the timeline. */
export function describeEvent(e: TimelineEntry): string {
  const d = e.details as Record<string, unknown>;
  switch (e.event_type) {
    case "case.created":
      return "You started the case.";
    case "intake.completed":
      return "Recourse read your evidence.";
    case "intake.not_payment_evidence":
      return "The upload didn't look like a payment, so the case was stopped.";
    case "case.facts_reviewed":
      return "You checked the details.";
    case "case.assessed":
      return "The case was assessed against the RBI rules.";
    case "case.reassessed":
      return "What you're owed was brought up to date.";
    case "draft.ready":
      return d.kind === "OMBUDSMAN_COMPLAINT"
        ? "Your complaint to the RBI Ombudsman was drafted and checked."
        : "Your letter to the bank was drafted and checked.";
    case "draft.failed":
      return "A letter could not be checked, so none was shown.";
    case "draft.approved":
      return "You approved the letter.";
    case "draft.rejected":
      return "You asked for a new draft.";
    case "case.bank_selected":
      return "You chose your bank.";
    case "email.prepared":
      return d.sandbox ? "The email was prepared (sandbox)." : "The email was prepared.";
    case "email.blocked":
      return "The email could not be prepared yet.";
    case "email.marked_sent":
      return "You sent the letter to the bank.";
    case "email.fallback_manual":
      return "Gmail wasn't available, so the letter is ready for you to send yourself.";
    case "gmail.messages.send":
      return d.status === 200 ? "Gmail: the letter was sent from your account." : "Gmail: sending did not succeed.";
    case "gmail.threads.get":
      return "Gmail: checked this email's thread for a reply.";
    case "gmail.oauth.refresh":
      return d.ok ? "Gmail: access renewed for one check." : "Gmail: access was refused (disconnected or revoked).";
    case "email.sent":
      return d.sandbox ? "The letter was sent from your Gmail (to the sandbox inbox)." : "The letter was sent from your Gmail.";
    case "bank.wait_started":
      return "The bank's reply window started.";
    case "bank.wait_expired":
      return "The bank's reply window ended.";
    case "bank.replied":
      return "The deadlines were updated after the bank's reply.";
    case "reply.received":
      return d.source === "GMAIL" ? "A new message arrived in the email's Gmail thread." : "A reply from the bank was added.";
    case "reply.judged":
      return "The bank's reply was checked against the rules.";
    case "reply.suspicious":
      return "A reply looked like a scam. Nothing else happened.";
    case "reply.marked_unsatisfactory":
      return "You decided the bank's reply was not good enough.";
    case "case.escalation_chosen":
      return "You chose to go to the RBI Ombudsman.";
    case "escalation.checked":
      return "Recourse checked that you can go to the RBI Ombudsman.";
    case "case.bank_type_given":
      return "You told Recourse what kind of bank it is.";
    case "ombudsman.filed":
      return "You filed the complaint with the RBI Ombudsman.";
    case "case.resolved":
      return "You received your money. Case resolved.";
    case "case.closed":
      return "The case was closed.";
    case "case.abandoned":
      return "The case was stopped.";
    default:
      return e.event_type;
  }
}
