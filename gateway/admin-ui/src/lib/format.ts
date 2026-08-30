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
