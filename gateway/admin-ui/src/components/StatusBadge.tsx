import { cn } from "../lib/cn";
import type { InstanceState } from "../types/instance";

const STATE_STYLES: Record<InstanceState, string> = {
  ready: "bg-green-100 text-green-700 dark:bg-green-500/15 dark:text-green-400",
  loading: "bg-yellow-100 text-yellow-700 dark:bg-yellow-500/15 dark:text-yellow-400",
  paused: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400",
  stopped: "bg-gray-100 text-gray-700 dark:bg-gray-500/15 dark:text-gray-300",
  error: "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400",
};

export function StatusBadge({
  state,
  message,
}: {
  state: InstanceState;
  /** Shown as a tooltip — e.g. why a model is in the "error" state. */
  message?: string | null;
}) {
  return (
    <span
      title={message ?? undefined}
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize",
        state === "error" && message && "cursor-help",
        STATE_STYLES[state],
      )}
    >
      {state}
    </span>
  );
}
