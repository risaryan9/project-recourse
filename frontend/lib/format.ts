/** Indian digit grouping, as in the backend's letters: 125000 -> "₹1,25,000.00". */
export function formatINR(amount: number, { paise = true }: { paise?: boolean } = {}): string {
  const fixed = Math.abs(amount).toFixed(paise ? 2 : 0);
  const [rupees, decimals] = fixed.split(".");
  let grouped = rupees;
  if (rupees.length > 3) {
    const head = rupees.slice(0, -3);
    const tail = rupees.slice(-3);
    grouped = `${head.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${tail}`;
  }
  return `${amount < 0 ? "-" : ""}₹${grouped}${decimals ? `.${decimals}` : ""}`;
}

/** "2026-11-20" or an ISO date-time -> "20 Nov 2026" (dates are calendar dates in IST). */
export function formatDate(value: string | null | undefined, { long = false } = {}): string {
  if (!value) return "";
  const d = new Date(value.length === 10 ? `${value}T00:00:00+05:30` : value);
  return d.toLocaleDateString("en-IN", {
    day: "numeric",
    month: long ? "long" : "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "";
  return new Date(value).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Kolkata",
  });
}

/** A Decimal string from the API ("2950.00") as rupees. */
export function inr(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  return formatINR(Number(value));
}

/** Whole calendar days from today (IST) to a date; negative if it has passed. */
export function daysFromToday(date: string): number {
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  return Math.round((Date.parse(date) - Date.parse(today)) / 86_400_000);
}
