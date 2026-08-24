import type { InstanceEntry } from "../types/instance";
import { InstanceRow } from "./InstanceRow";

const COLUMNS = ["ID", "Node", "Backend", "Modality", "State", "Port", "CPU", "RSS", "Uptime", "Actions"];

export function InstanceTable({ instances }: { instances: InstanceEntry[] }) {
  if (instances.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
        No models registered yet — click "Register model" to add one.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface">
      <table className="w-full min-w-[900px] text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
            {COLUMNS.map((col) => (
              <th key={col} className="px-4 py-3 font-medium">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {instances.map((instance) => (
            <InstanceRow key={`${instance.node}-${instance.id}`} instance={instance} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
