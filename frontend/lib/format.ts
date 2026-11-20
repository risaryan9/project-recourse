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
