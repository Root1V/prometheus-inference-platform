import { ChevronLeft, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import type { InstanceEntry, InstanceState } from "../types/instance";
import { InstanceRow } from "./InstanceRow";

const COLUMNS = ["#", "ID", "Node", "Backend", "Modality", "State", "Port", "CPU", "RSS", "Uptime", "Actions"];

const PAGE_SIZE = 20;

// RM-26: "active-first" — running/starting/broken instances surface above the
// normal "stopped" resting state, which otherwise buries them once a node has
// many registered-but-idle models.
const STATE_RANK: Record<InstanceState, number> = {
  ready: 0,
  loading: 1,
  error: 2,
  paused: 3,
  stopped: 4,
};

export function InstanceTable({
  instances,
  onEdit,
}: {
  instances: InstanceEntry[];
  onEdit: (instance: InstanceEntry) => void;
}) {
  const [page, setPage] = useState(1);

  const sorted = useMemo(
    () => [...instances].sort((a, b) => STATE_RANK[a.state] - STATE_RANK[b.state]),
    [instances],
  );

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const startIndex = (currentPage - 1) * PAGE_SIZE;
  const pageItems = sorted.slice(startIndex, startIndex + PAGE_SIZE);

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
          {pageItems.map((instance, i) => (
            <InstanceRow
              key={`${instance.node}-${instance.id}`}
              rowNumber={startIndex + i + 1}
              instance={instance}
              onEdit={onEdit}
            />
          ))}
        </tbody>
      </table>

      {totalPages > 1 && (
        <div className="flex items-center justify-between border-t border-border px-4 py-3 text-sm text-text-muted">
          <span>
            Showing {startIndex + 1}–{Math.min(startIndex + PAGE_SIZE, sorted.length)} of {sorted.length}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={currentPage === 1}
              className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 hover:bg-background disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronLeft size={14} />
              Prev
            </button>
            <span>
              Page {currentPage} of {totalPages}
            </span>
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={currentPage === totalPages}
              className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 hover:bg-background disabled:cursor-not-allowed disabled:opacity-40"
            >
              Next
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
