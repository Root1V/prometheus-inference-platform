import type { InstanceEntry } from "../types/instance";
import { CircuitBadge, type CircuitState } from "./CircuitBadge";
import { StatusBadge } from "./StatusBadge";

export interface AttentionEntry {
  instance: InstanceEntry;
  circuitState?: CircuitState;
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
