import { cn } from "../lib/cn";
import type { InstanceEntry } from "../types/instance";
import { StatusBadge } from "./StatusBadge";

export interface AttentionEntry {
  instance: InstanceEntry;
  circuitState?: "closed" | "open" | "half-open" | "unknown";
}

const CIRCUIT_STYLES: Record<string, string> = {
  closed: "bg-green-100 text-green-700",
  open: "bg-red-100 text-red-700",
  "half-open": "bg-yellow-100 text-yellow-700",
};

function CircuitBadge({ state }: { state?: AttentionEntry["circuitState"] }) {
  const label = state ?? "unknown";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize",
        CIRCUIT_STYLES[label] ?? "bg-gray-100 text-gray-700",
      )}
    >
      {label}
    </span>
  );
}

export function AttentionTable({ entries }: { entries: AttentionEntry[] }) {
  if (entries.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-surface p-6 text-center text-sm text-text-muted">
        All models healthy — nothing needs attention right now.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface">
      <table className="w-full min-w-[560px] text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
            <th className="px-4 py-3 font-medium">Model</th>
            <th className="px-4 py-3 font-medium">Node</th>
            <th className="px-4 py-3 font-medium">State</th>
            <th className="px-4 py-3 font-medium">Circuit</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(({ instance, circuitState }) => (
            <tr key={`${instance.node}-${instance.id}`} className="border-b border-border last:border-0">
              <td className="px-4 py-3 font-medium text-text">{instance.id}</td>
              <td className="px-4 py-3 text-text-muted">{instance.node}</td>
              <td className="px-4 py-3">
                <StatusBadge state={instance.state} message={instance.error_message} />
              </td>
              <td className="px-4 py-3">
                <CircuitBadge state={circuitState} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
