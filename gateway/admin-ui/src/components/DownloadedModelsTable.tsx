import { Trash2 } from "lucide-react";
import { useState } from "react";
import { useDeleteDownloadedModel } from "../api/models";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import { formatBytes } from "../lib/format";
import type { InstanceEntry } from "../types/instance";
import { Badge, ModalityBadge } from "./Badge";
import { ConfirmDialog } from "./ConfirmDialog";
import { StatusBadge } from "./StatusBadge";

const COLUMNS = ["Name", "Modality", "Family", "Quantization", "Context", "Size", "Status", "Actions"];

function DownloadedModelRow({
  model,
  node,
  selected,
  onSelect,
}: {
  model: InstanceEntry;
  node: string;
  selected: boolean;
  onSelect: () => void;
}) {
  const { showToast } = useToast();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const deleteDownloaded = useDeleteDownloadedModel();

  return (
    <>
      <tr
        onClick={onSelect}
        className={cn(
          "cursor-pointer border-b border-border last:border-0 hover:bg-background",
          selected && "bg-background ring-1 ring-inset ring-primary",
        )}
      >
        <td className="px-4 py-3 font-medium text-text">{model.id}</td>
        <td className="px-4 py-3">
          <ModalityBadge modality={model.modality} />
        </td>
        <td className="px-4 py-3 text-text-muted">{model.family || "—"}</td>
        <td className="px-4 py-3">
          <Badge>{model.quantization || "—"}</Badge>
        </td>
        <td className="px-4 py-3 text-text-muted">
          {model.context_length > 0 ? model.context_length.toLocaleString() : "—"}
        </td>
        <td className="px-4 py-3 text-text-muted">{formatBytes(model.file_size_bytes)}</td>
        <td className="px-4 py-3">
          <StatusBadge state={model.state} message={model.error_message} />
        </td>
        <td className="px-4 py-3">
          <button
            type="button"
            title="Delete downloaded file"
            aria-label={`Delete ${model.id}`}
            disabled={deleteDownloaded.isPending}
            onClick={(e) => {
              e.stopPropagation();
              setConfirmDelete(true);
            }}
            className="rounded-md p-1.5 text-text-muted transition-colors hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <Trash2 size={16} />
          </button>
        </td>
      </tr>
      <ConfirmDialog
        open={confirmDelete}
        title={`Delete ${model.id}?`}
        description="This permanently removes the downloaded file(s) from disk. This cannot be undone."
        confirmLabel="Delete"
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => {
          setConfirmDelete(false);
          deleteDownloaded.mutate(
            { node, modelId: model.id },
            {
              onSuccess: () => showToast(`${model.id} deleted`, "success"),
              onError: (e) => showToast(getErrorMessage(e), "error"),
            },
          );
        }}
      />
    </>
  );
}

export function DownloadedModelsTable({
  models,
  node,
  selectedId,
  onSelect,
}: {
  models: InstanceEntry[];
  node: string;
  selectedId: string | null;
  onSelect: (model: InstanceEntry) => void;
}) {
  if (models.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
        Nothing downloaded yet — switch to the Discover tab to get started.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface">
      <table className="w-full min-w-[760px] text-left text-sm">
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
          {models.map((m) => (
            <DownloadedModelRow
              key={m.id}
              model={m}
              node={node}
              selected={m.id === selectedId}
              onSelect={() => onSelect(m)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
