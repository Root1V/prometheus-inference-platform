import { ChevronDown, Pencil, Play, RotateCw, Square, Terminal, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  useDeleteModel,
  useInstanceLogs,
  useRestartInstance,
  useStartInstance,
  useStopInstance,
} from "../api/instances";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { formatUptime } from "../lib/format";
import { getErrorMessage } from "../lib/errors";
import type { InstanceEntry } from "../types/instance";
import { ConfirmDialog } from "./ConfirmDialog";
import { StatusBadge } from "./StatusBadge";

const COLUMN_COUNT = 11; // #, ID, Node, Backend, Modality, State, Port, CPU, RSS, Uptime, Actions

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

export function InstanceRow({
  rowNumber,
  instance,
  onEdit,
}: {
  rowNumber: number;
  instance: InstanceEntry;
  onEdit: (instance: InstanceEntry) => void;
}) {
  const { showToast } = useToast();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showLogs, setShowLogs] = useState(false);
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
      <tr className="border-b border-border last:border-0">
        <td className="px-4 py-3 text-text-muted">{rowNumber}</td>
        <td className="px-4 py-3 font-medium text-text">{instance.id}</td>
        <td className="px-4 py-3 text-text-muted">{instance.node}</td>
        <td className="px-4 py-3 text-text-muted">{instance.backend}</td>
        <td className="px-4 py-3 text-text-muted capitalize">{instance.modality}</td>
        <td className="px-4 py-3">
          <StatusBadge state={instance.state} message={instance.error_message} />
        </td>
        <td className="px-4 py-3 text-text-muted">{instance.port}</td>
        <td className="px-4 py-3 text-text-muted">{instance.cpu_percent.toFixed(1)}%</td>
        <td className="px-4 py-3 text-text-muted">{Math.round(instance.rss_mb)} MB</td>
        <td className="px-4 py-3 text-text-muted">{formatUptime(instance.uptime_s)}</td>
        <td className="px-4 py-3">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              title="Start"
              aria-label={`Start ${instance.id}`}
              disabled={instance.state === "ready" || isBusy}
              onClick={() => runAction(start.mutateAsync, `${instance.id} started`)}
              className={cn(actionButtonClass, "text-green-600 hover:bg-green-50")}
            >
              <Play size={16} />
            </button>
            <button
              type="button"
              title="Stop"
              aria-label={`Stop ${instance.id}`}
              disabled={instance.state === "stopped" || isBusy}
              onClick={() => runAction(stop.mutateAsync, `${instance.id} stopped`)}
              className={cn(actionButtonClass, "text-gray-600 hover:bg-gray-100")}
            >
              <Square size={16} />
            </button>
            <button
              type="button"
              title="Restart"
              aria-label={`Restart ${instance.id}`}
              disabled={instance.state === "stopped" || isBusy}
              onClick={() => runAction(restart.mutateAsync, `${instance.id} restarted`)}
              className={cn(actionButtonClass, "text-blue-600 hover:bg-blue-50")}
            >
              <RotateCw size={16} />
            </button>
            <button
              type="button"
              title="Edit"
              aria-label={`Edit ${instance.id}`}
              disabled={isBusy}
              onClick={() => onEdit(instance)}
              className={cn(actionButtonClass, "text-text-muted hover:bg-background")}
            >
              <Pencil size={16} />
            </button>
            <button
              type="button"
              title="Delete"
              aria-label={`Delete ${instance.id}`}
              disabled={isBusy}
              onClick={() => setConfirmDelete(true)}
              className={cn(actionButtonClass, "text-red-600 hover:bg-red-50")}
            >
              <Trash2 size={16} />
            </button>
            <button
              type="button"
              title="Logs"
              aria-label={`${showLogs ? "Hide" : "Show"} logs for ${instance.id}`}
              onClick={() => setShowLogs((v) => !v)}
              className={cn(
                actionButtonClass,
                showLogs ? "text-primary" : "text-text-muted hover:bg-background",
              )}
            >
              <Terminal size={16} />
            </button>
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
