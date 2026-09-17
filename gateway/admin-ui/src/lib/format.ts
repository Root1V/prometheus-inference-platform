/** Formats a duration in seconds as e.g. "2h 14m" (or "14m" under an hour). */
export function formatUptime(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds) || totalSeconds <= 0) return "0m";
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  return hours === 0 ? `${minutes}m` : `${hours}h ${minutes}m`;
}

/** Formats a "seconds ago" duration as e.g. "just now", "5m ago", "2h ago". */
export function formatAgo(seconds: number): string {
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

/** Formats a byte count as e.g. "1.2 GB", or "—" for null/unknown. */
export function formatBytes(n: number | null): string {
  if (n === null || n <= 0) return n === null ? "—" : "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(1)} ${units[i]}`;
}

/** Decimals needed for `amount` to show two significant digits — PRM-122.
 *
 * Four decimals is plenty for a period total and far too few for one request:
 * 12 prompt + 12 completion tokens at the base rate costs 0.0000096 USD, which
 * rounds to "USD 0.00" and reads as free. A per-request row is where this
 * platform's money lives, and a charge displayed as zero is the same lie as a
 * period total displayed as zero (PRM-119) — one row down.
 *
 * Never fewer than `floor`, so every figure that already read well still does;
 * a true zero keeps showing as 0.00, because that one *is* zero.
 */
function significantDecimals(amount: number, floor = 4): number {
  if (!Number.isFinite(amount) || amount === 0) return floor;
  const magnitude = Math.floor(Math.log10(Math.abs(amount)));
  return Math.min(20, Math.max(floor, -magnitude + 1));
}

/** Formats a USD cost, or "—" for null (docs/roadmap.md RM-33: null means "no price configured"). */
export function formatUsdCost(cost: number | null): string {
  if (cost === null) return "—";
  return cost.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: significantDecimals(cost),
  });
}

export type CurrencyCode = "USD" | "PEN" | "EUR";

/**
 * RM-60: display-only multi-currency formatting — cost is always stored and
 * computed in USD (see gateway/pricing.yaml); `unitsPerUsd` (1 for USD) just
 * converts the already-computed USD figure for display, never re-derives it.
 */
export function formatCurrency(
  costUsd: number | null,
  currency: CurrencyCode,
  unitsPerUsd: number,
): string {
  if (costUsd === null) return "—";
  const amount = currency === "USD" ? costUsd : costUsd * unitsPerUsd;
  return amount.toLocaleString(undefined, {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    // PRM-122: converting to a weaker currency multiplies the figure, but a
    // small USD amount stays small in PEN or EUR — and rendering it as 0,00
    // there is worse, because that is the number a client reads as their bill.
    maximumFractionDigits: significantDecimals(amount),
  });
}
