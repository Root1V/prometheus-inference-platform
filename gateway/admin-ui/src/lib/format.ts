/** Formats a duration in seconds as e.g. "2h 14m" (or "14m" under an hour). */
export function formatUptime(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds) || totalSeconds <= 0) return "0m";
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  return hours === 0 ? `${minutes}m` : `${hours}h ${minutes}m`;
}

/** Formats a USD cost, or "—" for null (docs/roadmap.md RM-33: null means "no price configured"). */
export function formatUsdCost(cost: number | null): string {
  if (cost === null) return "—";
  return cost.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  });
}
