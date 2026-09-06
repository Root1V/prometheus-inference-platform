import { ArrowDown, ArrowUp, ArrowUpDown, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useDeleteDownloadedModel } from "../api/models";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import { formatBytes } from "../lib/format";
import type { InstanceEntry } from "../types/instance";
import type { ModelCatalogEntry } from "../types/models";
import { Badge } from "./Badge";
import { ConfirmDialog } from "./ConfirmDialog";

type SortKey = "family" | "size" | "instances";
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
  runningInstances,
  node,
  selected,
  onSelect,
}: {
  rowNumber: number;
  model: ModelCatalogEntry;
  /** Instances of this catalog entry that are currently running — drives
   * the delete confirmation's warning copy (RM-51: deleting cascades to
   * stop+remove every instance, so the operator needs to see that upfront). */
  runningInstances: InstanceEntry[];
  node: string;
  selected: boolean;
  onSelect: () => void;
}) {
  const { showToast } = useToast();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const deleteDownloaded = useDeleteDownloadedModel();
  const hasInstances = model.instance_ids.length > 0;

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
        <td className="px-4 py-3 text-text-muted">{model.family || "—"}</td>
        <td className="px-4 py-3">
          <Badge>{model.quantization || "—"}</Badge>
        </td>
        <td className="px-4 py-3 text-text-muted">{formatBytes(model.file_size_bytes)}</td>
        <td className="px-4 py-3 text-text-muted">
          {hasInstances ? model.instance_ids.length : "—"}
        </td>
        <td className="px-4 py-3">
          <div className="flex items-center gap-1">
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
        description={
          runningInstances.length > 0 ? (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
              <p className="font-medium">
                This model has {runningInstances.length} running instance(s):{" "}
                {runningInstances.map((i) => `${i.id} (${i.node})`).join(", ")}.
              </p>
              <p className="mt-1">
                Deleting will stop and remove all of them, then delete the downloaded
                file(s). This cannot be undone.
              </p>
            </div>
          ) : hasInstances ? (
            `This will remove ${model.instance_ids.length} stopped instance(s) and delete the downloaded file(s). This cannot be undone.`
          ) : (
            "This permanently removes the downloaded file(s) from disk. This cannot be undone."
          )
        }
        confirmLabel="Delete"
        requireTypedConfirmation={runningInstances.length > 0 ? model.id : undefined}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => {
          setConfirmDelete(false);
          deleteDownloaded.mutate(
            { node, modelId: model.id, confirm: hasInstances },
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
  instances,
  node,
  selectedId,
  onSelect,
}: {
  models: ModelCatalogEntry[];
  /** RM-51: all instances, cross-node — used to show which of a catalog
   * entry's instances are currently running before a cascade delete. */
  instances: InstanceEntry[];
  node: string;
  selectedId: string | null;
  onSelect: (model: ModelCatalogEntry) => void;
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
        case "family":
          return a.family.localeCompare(b.family) * dir;
        case "size":
          return ((a.file_size_bytes ?? -1) - (b.file_size_bytes ?? -1)) * dir;
        case "instances":
          return (a.instance_ids.length - b.instance_ids.length) * dir;
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
      <table className="w-full min-w-[720px] text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
            <th className="px-4 py-3 font-medium">#</th>
            <th className="px-4 py-3 font-medium">Name</th>
            <th className="px-4 py-3 font-medium">
              <SortableHeader label="Family" sortKey="family" {...sortProps} />
            </th>
            <th className="px-4 py-3 font-medium">Quantization</th>
            <th className="px-4 py-3 font-medium">
              <SortableHeader label="Size" sortKey="size" {...sortProps} />
            </th>
            <th className="px-4 py-3 font-medium">
              <SortableHeader label="Instances" sortKey="instances" {...sortProps} />
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
              runningInstances={instances.filter(
                (inst) => inst.model_id === m.id && inst.state === "ready",
              )}
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
