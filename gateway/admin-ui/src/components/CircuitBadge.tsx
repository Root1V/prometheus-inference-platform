import { cn } from "../lib/cn";

export type CircuitState = "closed" | "open" | "half-open" | "unknown";

const CIRCUIT_STYLES: Record<string, string> = {
  closed: "bg-green-100 text-green-700 dark:bg-green-500/15 dark:text-green-400",
  open: "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400",
  "half-open": "bg-yellow-100 text-yellow-700 dark:bg-yellow-500/15 dark:text-yellow-400",
};

const UNKNOWN_STYLE = "bg-gray-100 text-gray-700 dark:bg-gray-500/15 dark:text-gray-300";

export function CircuitBadge({ state }: { state?: CircuitState }) {
  const label = state ?? "unknown";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize",
        CIRCUIT_STYLES[label] ?? UNKNOWN_STYLE,
      )}
    >
      {label}
    </span>
  );
}
