import { cn } from "../lib/cn";
import type { InstanceState } from "../types/instance";

const STATE_STYLES: Record<InstanceState, string> = {
  ready: "bg-green-100 text-green-700",
  loading: "bg-yellow-100 text-yellow-700",
  paused: "bg-blue-100 text-blue-700",
  stopped: "bg-gray-100 text-gray-700",
  error: "bg-red-100 text-red-700",
};

export function StatusBadge({ state }: { state: InstanceState }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize",
        STATE_STYLES[state],
      )}
    >
      {state}
    </span>
  );
}
