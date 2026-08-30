import { ArrowDown, ArrowUp, ArrowUpDown, Pencil, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useDeleteDownloadedModel } from "../api/models";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import { formatBytes } from "../lib/format";
import type { InstanceEntry } from "../types/instance";
import { Badge, ModalityBadge } from "./Badge";
import { ConfirmDialog } from "./ConfirmDialog";
import { StatusBadge } from "./StatusBadge";

type SortKey = "status" | "family" | "modality" | "size";
type SortDir = "asc" | "desc";

function SortableHeader({
  label,
  sortKey,
  activeKey,
  dir,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  activeKey: SortKey | null;
  dir: SortDir;
  onSort: (key: SortKey) => void;
}) {
  const isActive = activeKey === sortKey;
  const Icon = isActive ? (dir === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
  return (
    <button
      type="button"
      onClick={() => onSort(sortKey)}
      className={cn(
        "flex items-center gap-1 font-medium",
        isActive ? "text-text" : "text-text-muted hover:text-text",
      )}
    >
      {label}
      <Icon size={12} />
    </button>
  );
}

function DownloadedModelRow({
  rowNumber,
  model,
  node,
  selected,
  onSelect,
  onEdit,
}: {
  rowNumber: number;
  model: InstanceEntry;
  node: string;
  selected: boolean;
  onSelect: () => void;
  onEdit: (model: InstanceEntry) => void;
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
        <td className="px-4 py-3 text-text-muted">{rowNumber}</td>
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
          <div className="flex items-center gap-1">
            <button
              type="button"
              title="Edit"
              aria-label={`Edit ${model.id}`}
              onClick={(e) => {
                e.stopPropagation();
                onEdit(model);
              }}
              className="rounded-md p-1.5 text-text-muted transition-colors hover:bg-background hover:text-text"
            >
              <Pencil size={16} />
            </button>
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
          </div>
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
  onEdit,
}: {
  models: InstanceEntry[];
  node: string;
  selectedId: string | null;
  onSelect: (model: InstanceEntry) => void;
  onEdit: (model: InstanceEntry) => void;
}) {
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  const sorted = useMemo(() => {
    if (!sortKey) return models;
    const dir = sortDir === "asc" ? 1 : -1;
    return [...models].sort((a, b) => {
      switch (sortKey) {
        case "status":
          return a.state.localeCompare(b.state) * dir;
        case "family":
          return a.family.localeCompare(b.family) * dir;
        case "modality":
          return a.modality.localeCompare(b.modality) * dir;
        case "size":
          return ((a.file_size_bytes ?? -1) - (b.file_size_bytes ?? -1)) * dir;
        default:
          return 0;
      }
    });
  }, [models, sortKey, sortDir]);

  if (models.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
        Nothing downloaded yet — switch to the Discover tab to get started.
      </div>
    );
  }

  const sortProps = { activeKey: sortKey, dir: sortDir, onSort: handleSort };

  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface">
      <table className="w-full min-w-[820px] text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
            <th className="px-4 py-3 font-medium">#</th>
            <th className="px-4 py-3 font-medium">Name</th>
            <th className="px-4 py-3 font-medium">
              <SortableHeader label="Modality" sortKey="modality" {...sortProps} />
            </th>
            <th className="px-4 py-3 font-medium">
              <SortableHeader label="Family" sortKey="family" {...sortProps} />
            </th>
            <th className="px-4 py-3 font-medium">Quantization</th>
            <th className="px-4 py-3 font-medium">Context</th>
            <th className="px-4 py-3 font-medium">
              <SortableHeader label="Size" sortKey="size" {...sortProps} />
            </th>
            <th className="px-4 py-3 font-medium">
              <SortableHeader label="Status" sortKey="status" {...sortProps} />
            </th>
            <th className="px-4 py-3 font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((m, i) => (
            <DownloadedModelRow
              key={m.id}
              rowNumber={i + 1}
              model={m}
              node={node}
              selected={m.id === selectedId}
              onSelect={() => onSelect(m)}
              onEdit={onEdit}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
