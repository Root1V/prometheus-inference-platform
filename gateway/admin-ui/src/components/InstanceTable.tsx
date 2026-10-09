import { cn } from "../lib/cn";
import { BulkActions } from "./InstanceBulkActions";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import type { BackendMetrics } from "../api/metrics";
import type { InstanceEntry, InstanceState } from "../types/instance";
import { InstanceRow } from "./InstanceRow";
import { TableSearchInput } from "./TableSearchInput";

/** RM-46 follow-up: header tooltips explain what each performance column
 * means once, here — the per-row cells no longer need to repeat it. */
/**
 * PRM-204: two column sets behind a toggle, replacing one row of eighteen.
 *
 * Six of those eighteen were performance metrics, and on this deployment they
 * are `—` for nine of twelve instances — a metric only exists once a request of
 * the right shape has happened, so an instance that has served nothing shows
 * nothing. Eighteen columns to carry four populated ones made every row scroll
 * sideways and nothing comparable.
 *
 * Health is the default because it is the question the page is opened with:
 * what is running, how much is it holding, and for how long.
 */
const HEALTH_COLUMNS: { label: string; title?: string }[] = [
  { label: "" },
  { label: "Instance", title: "The instance id, and underneath it the name clients send as `model`." },
  { label: "State" },
  { label: "Node · engine" },
  { label: "Context · port" },
  { label: "Memory", title: "Resident memory now, and the size of the weights on disk." },
  { label: "Uptime" },
  { label: "" },
];

const PERFORMANCE_COLUMNS: { label: string; title?: string }[] = [
  { label: "" },
  { label: "Instance" },
  { label: "State" },
  { label: "Requests", title: "Requests this instance has served since the gateway started." },
  {
    label: "Latency p50 / p95",
    title:
      "Median and 95th percentile. The p95 is the slow tail — only the worst 5% of requests took longer.",
  },
  {
    label: "TTFT",
    title: "Time to first token. Streaming requests only; blank where nothing streamed.",
  },
  {
    label: "Throughput",
    title:
      "Tokens per second for text, images per second for image models, and the inter-token latency underneath where the engine reports it.",
  },
  { label: "" },
];


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
  backendMetrics,
  onEdit,
  onAddInstance,
}: {
  instances: InstanceEntry[];
  /** RM-46: keyed by instance/backend id, same as InstanceEntry.id — absent
   * (undefined) while GET /metrics's first poll hasn't landed yet. */
  backendMetrics?: Record<string, BackendMetrics>;
  onEdit: (instance: InstanceEntry) => void;
  onAddInstance: (instance: InstanceEntry) => void;
}) {
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");

  const sorted = useMemo(
    () => [...instances].sort((a, b) => STATE_RANK[a.state] - STATE_RANK[b.state]),
    [instances],
  );

  // RM-59: client-side filter over the fields an operator actually scans for
  // when hunting a specific instance. Everything here is already in memory —
  // no refetch, no new endpoint.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q === "") return sorted;
    return sorted.filter((i) =>
      [i.id, i.model_id, i.node, i.backend, i.modality, i.state, String(i.port)].some((field) =>
        field.toLowerCase().includes(q),
      ),
    );
  }, [sorted, query]);

  const [view, setView] = useState<"health" | "performance">("health");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const columns = view === "health" ? HEALTH_COLUMNS : PERFORMANCE_COLUMNS;

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const startIndex = (currentPage - 1) * PAGE_SIZE;
  const pageItems = filtered.slice(startIndex, startIndex + PAGE_SIZE);

  const keyOf = (id: string, node: string) => `${node}/${id}`;
  const toggleOne = (id: string, node: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      const k = keyOf(id, node);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  const pageKeys = pageItems.map((i) => keyOf(i.id, i.node));
  const allOnPageSelected = pageKeys.length > 0 && pageKeys.every((k) => selected.has(k));
  const chosen = pageItems.filter((i) => selected.has(keyOf(i.id, i.node)));

  if (instances.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
        No instances yet — click "Register instance" to run one of your downloaded models.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface">
      <TableSearchInput
        value={query}
        onChange={(v) => {
          setQuery(v);
          // Reset to the first page — otherwise a filter that shrinks the
          // result set below the current page leaves the operator looking at
          // a page that no longer exists.
          setPage(1);
        }}
        placeholder="Filter by id, model, node, backend, modality, state or port…"
        resultLabel={`${filtered.length} of ${instances.length}`}
      />

      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-3 pb-3">
        <div role="tablist" aria-label="Column set" className="flex items-center gap-1 rounded-lg bg-background p-1">
          {(["health", "performance"] as const).map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              onClick={() => setView(v)}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium capitalize transition-colors",
                view === v ? "bg-surface text-text shadow-sm" : "text-text-muted hover:text-text",
              )}
            >
              {v}
            </button>
          ))}
        </div>

        {/*
          PRM-204: bulk start and stop. Restarting a node's models after a
          config change meant clicking through them one at a time, and the
          selection is keyed by node/id because instance ids are only unique
          within a node.
        */}
        {chosen.length > 0 && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-text-muted">{chosen.length} selected</span>
            <BulkActions instances={chosen} onDone={() => setSelected(new Set())} />
          </div>
        )}
      </div>

      {filtered.length === 0 ? (
        <div className="p-12 text-center text-text-muted">
          No instances match “{query.trim()}”.
        </div>
      ) : (
      <table className="w-full min-w-[900px] text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
            <th className="w-10 px-4 py-3">
              <input
                type="checkbox"
                checked={allOnPageSelected}
                onChange={() =>
                  setSelected((prev) => {
                    const next = new Set(prev);
                    if (allOnPageSelected) pageKeys.forEach((k) => next.delete(k));
                    else pageKeys.forEach((k) => next.add(k));
                    return next;
                  })
                }
                aria-label="Select all instances on this page"
                className="h-4 w-4 cursor-pointer accent-primary"
              />
            </th>
            {columns.slice(1).map((col, i) => (
              <th
                key={col.label || `col-${i}`}
                className={col.title ? "cursor-help px-4 py-3 font-medium" : "px-4 py-3 font-medium"}
                title={col.title}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {pageItems.map((instance) => (
            <InstanceRow
              focusKey={`instance:${instance.id}`}
              key={`${instance.node}-${instance.id}`}
              view={view}
              selected={selected.has(keyOf(instance.id, instance.node))}
              onToggleSelected={toggleOne}
              instance={instance}
              metrics={backendMetrics?.[instance.id]}
              onEdit={onEdit}
              onAddInstance={onAddInstance}
            />
          ))}
        </tbody>
      </table>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-between border-t border-border px-4 py-3 text-sm text-text-muted">
          <span>
            Showing {startIndex + 1}–{Math.min(startIndex + PAGE_SIZE, filtered.length)} of{" "}
            {filtered.length}
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
