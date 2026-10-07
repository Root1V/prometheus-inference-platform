import { ArrowDown, ArrowUp, ArrowUpDown, Pencil, Trash2 } from "lucide-react";
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
import { EditModelModal } from "./EditModelModal";
import { TableSearchInput } from "./TableSearchInput";

type SortKey = "family" | "size" | "instances";
type SortDir = "asc" | "desc";

/**
 * Absent for two different reasons, and they were drawn identically — PRM-209.
 *
 * A model with `downloaded: false` is served from somewhere else: there is no
 * file on this node, so a size or a quantization *cannot* exist. A model with
 * `downloaded: true` and a null size is on disk and the size simply was not
 * reported — measured on this platform, that is `promptguard2-tei` and
 * `minimax-m27-iq2m`, the latter a GGUF split across three shards.
 *
 * Both rendered as the same em dash, so "not applicable" and "we failed to
 * find out" were indistinguishable. The second is a gap worth chasing; the
 * first is nothing at all.
 */
function Absent({ downloaded }: { downloaded: boolean }) {
  return downloaded ? (
    <span
      className="text-amber-600 dark:text-amber-400"
      title="On disk, but its size was not reported"
    >
      ?
    </span>
  ) : (
    <span
      className="text-text-muted/50"
      title="Served from elsewhere — no file on this node"
    >
      n/a
    </span>
  );
}

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
  model,
  runningInstances,
  node,
  selected,
  onSelect,
}: {
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
  const [editing, setEditing] = useState(false);
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
        {/* PRM-111: the display name, not `id` — the registry key cannot
            change, so a model someone had already named still showed its id.
            PRM-112: the routing name below it is always rendered and always
            labelled. It was conditional on differing from the name, which made
            it a subtitle that appeared on some rows and not others with nothing
            saying what it was — so the one question it exists to answer ("which
            of these do I put in `model`?") had no reliable answer. */}
        <td className="px-4 py-3">
          <div className="font-medium text-text">{model.name || model.id}</div>
          <div className="text-xs text-text-muted">
            model: <span className="font-mono">{model.slug || model.id}</span>
          </div>
        </td>
        <td className="px-4 py-3 text-text-muted">
          {model.family || <Absent downloaded={model.downloaded} />}
        </td>
        {/* PRM-109: shown on the model, because that is what it belongs to.
            Every instance inherits this one answer. */}
        <td className="px-4 py-3">
          <Badge>{model.modality || "—"}</Badge>
        </td>
        <td className="px-4 py-3">
          {model.quantization ? (
            <Badge>{model.quantization}</Badge>
          ) : (
            <Absent downloaded={model.downloaded} />
          )}
        </td>
        <td className="px-4 py-3 text-text-muted">
          {model.file_size_bytes === null ? (
            <Absent downloaded={model.downloaded} />
          ) : (
            formatBytes(model.file_size_bytes)
          )}
        </td>
        <td className="px-4 py-3 text-text-muted">
          {hasInstances ? model.instance_ids.length : "—"}
        </td>
        <td className="px-4 py-3">
          <div className="flex items-center gap-1">
            <button
              type="button"
              title="Edit model"
              aria-label={`Edit ${model.id}`}
              onClick={(e) => {
                e.stopPropagation();
                setEditing(true);
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
      <EditModelModal
        open={editing}
        model={model}
        node={node}
        onClose={() => setEditing(false)}
      />
      <ConfirmDialog
        open={confirmDelete}
        title={`Delete ${model.id}?`}
        description={
          hasInstances ? (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
              <p className="font-medium">
                {runningInstances.length > 0
                  ? `This model has ${model.instance_ids.length} instance(s), ${runningInstances.length} of them running: ${runningInstances
                      .map((i) => `${i.id} (${i.node})`)
                      .join(", ")}.`
                  : `This model has ${model.instance_ids.length} instance(s) (all stopped).`}
              </p>
              <p className="mt-1">
                Deleting will stop and remove all of them, then delete the
                downloaded file(s). This cannot be undone.
              </p>
            </div>
          ) : (
            "This permanently removes the downloaded file(s) from disk. This cannot be undone."
          )
        }
        confirmLabel="Delete"
        requireTypedConfirmation={hasInstances ? model.id : undefined}
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
  const [query, setQuery] = useState("");
  /**
   * "What can I delete?" is the question this page exists for, and answering
   * it meant reading every row. On this platform 21 of 32 local models have no
   * instance and hold 443.5 GB — 85% of the disk the page manages.
   *
   * Turning it on also sorts by size, largest first, because an idle 363 MB
   * model and an idle 77.6 GB one are not the same finding.
   */
  const [idleOnly, setIdleOnly] = useState(false);

  function toggleIdleOnly() {
    setIdleOnly((on) => {
      if (!on) {
        setSortKey("size");
        setSortDir("desc");
      }
      return !on;
    });
  }

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  // RM-59: client-side filter — the catalog is already fully in memory, so
  // this needs no endpoint and no refetch. Applied before sorting so the
  // sort operates on what's actually shown.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    let rows = models;
    if (q !== "") {
      rows = rows.filter((m) =>
        [m.id, m.family, m.quantization].some((field) =>
          field.toLowerCase().includes(q),
        ),
      );
    }
    if (idleOnly) rows = rows.filter((m) => m.instance_ids.length === 0);
    return rows;
  }, [models, query, idleOnly]);

  /** Only the idle rows that actually occupy disk can be reclaimed. */
  const idleCount = useMemo(
    () => models.filter((m) => m.instance_ids.length === 0).length,
    [models],
  );

  const sorted = useMemo(() => {
    if (!sortKey) return filtered;
    const dir = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
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
  }, [filtered, sortKey, sortDir]);

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
      <div className="flex flex-wrap items-center gap-3 border-b border-border pr-4">
        <div className="min-w-[16rem] flex-1">
          <TableSearchInput
            value={query}
            onChange={setQuery}
            placeholder="Filter by name, family or quantization…"
            resultLabel={`${sorted.length} of ${models.length}`}
          />
        </div>
        {/* `|| idleOnly` is not redundant with the remount-on-node-change:
            the catalog polls, so the last idle model gaining an instance can
            drop `idleCount` to 0 while the filter is still on. A filter must
            never be the only thing between someone and their data while its
            own switch is off-screen. */}
        {(idleCount > 0 || idleOnly) && (
          <button
            type="button"
            onClick={toggleIdleOnly}
            aria-pressed={idleOnly}
            className={cn(
              "shrink-0 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
              idleOnly
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border text-text-muted hover:bg-background hover:text-text",
            )}
          >
            No instance ({idleCount})
          </button>
        )}
      </div>

      {sorted.length === 0 ? (
        <div className="p-12 text-center text-text-muted">
          {idleOnly && query.trim() === ""
            ? "Every model here has at least one instance."
            : `No models match \u201c${query.trim()}\u201d.`}
        </div>
      ) : (
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead>
            <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
              <th className="px-4 py-3 font-medium">Name</th>
              <th className="px-4 py-3 font-medium">
                <SortableHeader
                  label="Family"
                  sortKey="family"
                  {...sortProps}
                />
              </th>
              <th className="px-4 py-3 font-medium">Modality</th>
              <th className="px-4 py-3 font-medium">Quantization</th>
              <th className="px-4 py-3 font-medium">
                <SortableHeader label="Size" sortKey="size" {...sortProps} />
              </th>
              <th className="px-4 py-3 font-medium">
                <SortableHeader
                  label="Instances"
                  sortKey="instances"
                  {...sortProps}
                />
              </th>
              <th className="px-4 py-3 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((m) => (
              <DownloadedModelRow
                key={m.id}
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
      )}
    </div>
  );
}
