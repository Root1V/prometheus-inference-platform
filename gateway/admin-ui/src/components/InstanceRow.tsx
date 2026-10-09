import {
  ChevronDown,
  CopyPlus,
  MoreHorizontal,
  Pencil,
  Play,
  RotateCw,
  Square,
  Terminal,
  Trash2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  useDeleteModel,
  useInstanceLogs,
  useRestartInstance,
  useStartInstance,
  useStopInstance,
} from "../api/instances";
import type { BackendMetrics } from "../api/metrics";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { formatUptime } from "../lib/format";
import { getErrorMessage } from "../lib/errors";
import type { InstanceEntry } from "../types/instance";
import { ConfirmDialog } from "./ConfirmDialog";
import { StatusBadge } from "./StatusBadge";

/** checkbox, instance, state, four view-specific cells, actions. */
const COLUMN_COUNT = 8;

/** What a client puts in its `model` field. Not the instance id, and that
 * distinction has cost time: granting `model:qwen3-embedding-0-6b-q8-0-local`
 * does nothing, because the slug is `qwen3-embedding`. The page that lists
 * instances is where someone looks this up, and it was the one thing it did
 * not show. */
function ClientName({ slug, fallback }: { slug: string; fallback: string }) {
  const name = slug || fallback;
  return (
    <code className="rounded bg-background px-1 py-0.5 text-[11px] text-text-muted" title={`Clients send "${name}" as the model`}>
      {name}
    </code>
  );
}

function formatBytes(bytes: number | null): string | null {
  if (bytes === null || bytes <= 0) return null;
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}

function LogTail({ node, modelId }: { node: string; modelId: string }) {
  const logsQuery = useInstanceLogs(node, modelId, true);
  const lines = logsQuery.data?.lines ?? [];
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [logsQuery.data?.lines]);

  return (
    <div className="max-h-72 overflow-y-auto rounded-lg bg-gray-950 p-3 font-mono text-xs text-gray-200">
      {logsQuery.isLoading ? (
        <p className="text-gray-400">Loading logs…</p>
      ) : logsQuery.isError ? (
        <p className="text-red-400">{getErrorMessage(logsQuery.error)}</p>
      ) : lines.length === 0 ? (
        <p className="text-gray-400">No log output yet.</p>
      ) : (
        lines.map((line, i) => (
          <div key={i} className="whitespace-pre-wrap">
            {line}
          </div>
        ))
      )}
      <div ref={bottomRef} />
    </div>
  );
}

const actionButtonClass =
  "rounded-md p-1.5 transition-colors disabled:cursor-not-allowed disabled:opacity-30";

const menuItemClass =
  "flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40";

export function InstanceRow({
  focusKey,
  instance,
  metrics,
  view,
  selected,
  onToggleSelected,
  onEdit,
  onAddInstance,
}: {
  /** PRM-248: so a search result can ring this row on arrival. */
  focusKey?: string;
  instance: InstanceEntry;
  view: "health" | "performance";
  selected: boolean;
  onToggleSelected: (id: string, node: string) => void;
  /** RM-46: undefined until GET /metrics's first poll lands, or if this
   * instance has never actually served a request yet (no samples recorded). */
  metrics?: BackendMetrics;
  onEdit: (instance: InstanceEntry) => void;
  onAddInstance: (instance: InstanceEntry) => void;
}) {
  const { showToast } = useToast();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showLogs, setShowLogs] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [menuOpen]);
  const start = useStartInstance();
  const stop = useStopInstance();
  const restart = useRestartInstance();
  const remove = useDeleteModel();

  const args: { node: string; modelId: string } = { node: instance.node, modelId: instance.id };
  const isBusy = start.isPending || stop.isPending || restart.isPending || remove.isPending;

  const runAction = (
    mutateAsync: (variables: typeof args) => Promise<unknown>,
    successMessage: string,
  ) => {
    mutateAsync(args).then(
      () => showToast(successMessage, "success"),
      (error: unknown) => showToast(getErrorMessage(error), "error"),
    );
  };

  return (
    <>
      <tr
        data-focus={focusKey}
        className="group/row border-b border-border last:border-0 hover:bg-background/60"
      >
        <td className="w-10 px-4 py-3">
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggleSelected(instance.id, instance.node)}
            aria-label={`Select ${instance.id}`}
            className="h-4 w-4 cursor-pointer accent-primary"
          />
        </td>

        <td className="min-w-[14rem] px-4 py-3">
          <div className="font-medium text-text">{instance.id}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-text-muted">
            <ClientName slug={instance.model_slug} fallback={instance.model_id} />
            {instance.label && instance.label !== "#1" && <span>{instance.label}</span>}
          </div>
        </td>

        <td className="px-4 py-3">
          <StatusBadge state={instance.state} message={instance.error_message} />
          {/* PRM-204: the reason, not just the colour. `error_message` was in
              the API and rendered nowhere — a failed start showed a red badge
              and left you to go read the logs to find out why. */}
          {instance.state === "error" && instance.error_message && (
            <div
              className="mt-1 max-w-[18rem] truncate text-xs text-red-600 dark:text-red-400"
              title={instance.error_message}
            >
              {instance.error_message}
            </div>
          )}
        </td>

        {view === "health" ? (
          <>
            <td className="px-4 py-3 text-xs text-text-muted">
              <div>{instance.node}</div>
              <div className="mt-0.5">
                {instance.backend} · <span className="capitalize">{instance.modality}</span>
              </div>
            </td>
            <td className="px-4 py-3 text-xs text-text-muted">
              {/* PRM-194 made this number a decision rather than a default, so
                  it belongs where instances are compared. */}
              <div>{instance.context_length.toLocaleString()} ctx</div>
              <div className="mt-0.5">:{instance.port}</div>
            </td>
            <td className="px-4 py-3 text-xs text-text-muted">
              <div>
                {Math.round(instance.rss_mb)} MB
                {instance.cpu_percent > 0 && ` · ${instance.cpu_percent.toFixed(1)}%`}
              </div>
              {formatBytes(instance.file_size_bytes) && (
                <div className="mt-0.5" title="Size on disk">
                  {formatBytes(instance.file_size_bytes)} on disk
                </div>
              )}
            </td>
            <td className="px-4 py-3 text-xs text-text-muted">
              {instance.uptime_s > 0 ? formatUptime(instance.uptime_s) : "—"}
            </td>
          </>
        ) : (
          <>
            <td className="px-4 py-3 text-xs text-text-muted">
              {metrics && metrics.requests_total > 0 ? metrics.requests_total.toLocaleString() : "—"}
            </td>
            <td className="px-4 py-3 text-xs text-text-muted">
              {metrics && metrics.requests_total > 0
                ? `${metrics.latency_p50_ms}ms / ${metrics.latency_p95_ms}ms`
                : "—"}
            </td>
            <td className="px-4 py-3 text-xs text-text-muted">
              {metrics?.ttft_p50_ms != null ? `${metrics.ttft_p50_ms}ms` : "—"}
            </td>
            <td className="px-4 py-3 text-xs text-text-muted">
              {metrics?.tokens_per_second_avg != null
                ? `${metrics.tokens_per_second_avg} tok/s`
                : metrics?.images_per_second_avg != null
                  ? `${metrics.images_per_second_avg} img/s`
                  : "—"}
              {metrics?.inter_token_ms_avg != null && (
                <div className="mt-0.5">{metrics.inter_token_ms_avg} ms/tok</div>
              )}
            </td>
          </>
        )}

        <td className="px-4 py-3">
          {/*
            PRM-204: one contextual button and a menu, replacing seven bare
            icons. Start and Stop were separate buttons of which exactly one was
            ever enabled, so the row carried a permanently dead control; Delete
            sat two icons from Restart, distinguishable only by glyph. The
            primary action is now whichever one applies, and everything that
            destroys or duplicates is behind a labelled second step.
          */}
          <div className="flex items-center justify-end gap-1">
            {instance.state === "stopped" || instance.state === "error" ? (
              <button
                type="button"
                title="Start"
                aria-label={`Start ${instance.id}`}
                disabled={isBusy}
                onClick={() => runAction(start.mutateAsync, `${instance.id} started`)}
                className={cn(actionButtonClass, "text-green-600 hover:bg-green-50 dark:hover:bg-green-950/40")}
              >
                <Play size={16} />
              </button>
            ) : (
              <button
                type="button"
                title="Stop"
                aria-label={`Stop ${instance.id}`}
                disabled={isBusy}
                onClick={() => runAction(stop.mutateAsync, `${instance.id} stopped`)}
                className={cn(actionButtonClass, "text-text-muted hover:bg-background hover:text-text")}
              >
                <Square size={16} />
              </button>
            )}
            <button
              type="button"
              title={showLogs ? "Hide logs" : "Show logs"}
              aria-label={`${showLogs ? "Hide" : "Show"} logs for ${instance.id}`}
              onClick={() => setShowLogs((v) => !v)}
              className={cn(
                actionButtonClass,
                showLogs ? "bg-background text-text" : "text-text-muted hover:bg-background hover:text-text",
              )}
            >
              <Terminal size={16} />
            </button>
            <div className="relative" ref={menuRef}>
              <button
                type="button"
                aria-label={`More actions for ${instance.id}`}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                disabled={isBusy}
                onClick={() => setMenuOpen((o) => !o)}
                className={cn(
                  actionButtonClass,
                  "text-text-muted hover:bg-background hover:text-text",
                  menuOpen && "bg-background text-text",
                )}
              >
                <MoreHorizontal size={16} />
              </button>
              {menuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 z-20 mt-1 w-56 overflow-hidden rounded-lg border border-border bg-surface py-1 shadow-lg"
                >
                  <button
                    type="button"
                    role="menuitem"
                    disabled={instance.state === "stopped"}
                    onClick={() => {
                      setMenuOpen(false);
                      runAction(restart.mutateAsync, `${instance.id} restarted`);
                    }}
                    className={cn(menuItemClass, "text-text hover:bg-background")}
                  >
                    <RotateCw size={15} className="text-text-muted" />
                    Restart
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      onEdit(instance);
                    }}
                    className={cn(menuItemClass, "text-text hover:bg-background")}
                  >
                    <Pencil size={15} className="text-text-muted" />
                    Edit
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      onAddInstance(instance);
                    }}
                    className={cn(menuItemClass, "text-text hover:bg-background")}
                  >
                    <CopyPlus size={15} className="text-text-muted" />
                    Add another replica
                  </button>
                  <div className="my-1 border-t border-border" />
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      setConfirmDelete(true);
                    }}
                    className={cn(
                      menuItemClass,
                      "text-red-700 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40",
                    )}
                  >
                    <Trash2 size={15} />
                    Delete instance
                  </button>
                </div>
              )}
            </div>
          </div>
        </td>
      </tr>
      {showLogs && (
        <tr className="border-b border-border bg-background/30 last:border-0">
          <td colSpan={COLUMN_COUNT} className="px-4 py-3">
            <div className="mb-1.5 flex items-center gap-1.5 text-xs text-text-muted">
              <ChevronDown size={12} />
              {instance.id} — last 200 lines, refreshing every 3s
            </div>
            <LogTail node={instance.node} modelId={instance.id} />
          </td>
        </tr>
      )}
      <ConfirmDialog
        open={confirmDelete}
        title={`Delete ${instance.id}?`}
        description="This stops the instance if running and permanently removes its registry entry. This cannot be undone."
        confirmLabel="Delete"
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => {
          setConfirmDelete(false);
          runAction(remove.mutateAsync, `${instance.id} deleted`);
        }}
      />
    </>
  );
}
