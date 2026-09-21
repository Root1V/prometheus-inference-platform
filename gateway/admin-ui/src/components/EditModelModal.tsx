import { Pencil, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useUpdateCatalogEntry } from "../api/models";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import type { Modality } from "../types/instance";
import type { ModelCatalogEntry } from "../types/models";

interface EditModelModalProps {
  open: boolean;
  model: ModelCatalogEntry;
  node: string;
  onClose: () => void;
}

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none";

const MODALITIES: Modality[] = ["text", "vision", "embedding", "image", "rerank", "classification"];

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block text-sm text-text">
      <span className="mb-1 block text-xs font-medium text-text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-text-muted">{hint}</span>}
    </label>
  );
}

/**
 * PRM-109: modality is edited here, on the model, and nowhere else.
 *
 * It is a property of the weights, so every instance of this model inherits the
 * same answer — which is what the instance form used to let you break. The
 * server still has the final say: a file that declares a classifier head or a
 * pooling type refuses a modality that contradicts it (PRM-107), so a wrong
 * choice here comes back as an error rather than as a model that answers with
 * confident nonsense.
 */
export function EditModelModal({ open, model, node, onClose }: EditModelModalProps) {
  const { showToast } = useToast();
  const updateCatalog = useUpdateCatalogEntry();
  const [modality, setModality] = useState<Modality>(model.modality || "text");
  const [family, setFamily] = useState(model.family || "");
  const [name, setName] = useState(model.name || model.id);
  const [slug, setSlug] = useState(model.slug || model.id);
  // PRM-114: the server decides. This used to ask "is the slug still the id",
  // which is the rule PRM-113 replaced — it let you type a new name for a model
  // with grants and billed history and only told you on save. The real question
  // is whether anything depends on the current name, and only the server can
  // see all three things that can.
  const blockers = model.rename_blockers ?? [];
  const unnamed = blockers.length === 0;

  if (!open) return null;

  const dirty =
    modality !== model.modality ||
    family.trim() !== (model.family || "") ||
    name.trim() !== (model.name || model.id) ||
    (unnamed && slug.trim() !== (model.slug || model.id));

  const handleSave = () => {
    updateCatalog.mutate(
      {
        node,
        modelId: model.id,
        data: {
          name: name.trim(),
          family: family.trim(),
          modality,
          // Only when it can still be named — sending an unchanged slug is
          // refused as a rename, which would fail the whole save.
          ...(unnamed && slug.trim() !== model.id ? { slug: slug.trim() } : {}),
        },
      },
      {
        onSuccess: () => {
          showToast(`${model.id} updated`, "success");
          onClose();
        },
        onError: (err) => showToast(getErrorMessage(err), "error"),
      },
    );
  };

  const runningWarning = model.instance_ids.length > 0;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-8">
      <div className="w-full max-w-md rounded-xl bg-surface p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-text">
            <Pencil size={18} />
            Edit model — {model.id}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={18} className="text-text-muted" />
          </button>
        </div>

        <div className="space-y-4">
          {/* PRM-111: the display label. `id` is the registry key and cannot
              change; `slug` is what clients route on and RM-70 lets it be set
              exactly once, so neither is edited here. This is the one that is
              safe to rename as often as you like. */}
          <Field label="Name" hint="Display label. The id and the routing name below do not change.">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={inputClass}
            />
          </Field>
          {/* PRM-112: the public name lives here now, not on an instance. */}
          <Field
            label="Public name (what clients send as `model`)"
            hint={
              unnamed
                ? "Free to change — nothing depends on this name yet. Once a client holds a grant for it, it is billed under it, or it has a price, it stays put."
                : `Cannot be changed: ${blockers.join("; ")}.`
            }
          >
            <input
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              disabled={!unnamed}
              pattern="^[a-zA-Z0-9][a-zA-Z0-9._-]{1,62}[a-zA-Z0-9]$"
              title="Letters, digits, dots, hyphens, underscores"
              className={cn(inputClass, !unnamed && "cursor-not-allowed opacity-60")}
            />
          </Field>
          <div className="rounded-lg border border-border bg-background px-3 py-2 text-xs text-text-muted">
            registry id <span className="font-mono text-text">{model.id}</span>
          </div>
          {/* PRM-110: family is read by people and nothing keys off it. The
              fallback is the GGUF's own architecture, which is true about the
              file but not always the lineage a human would name — phi4-mini is
              architecture phi3. That makes it exactly the field worth editing. */}
          <Field label="Family" hint="What people see. Defaults to the file's architecture.">
            <input
              value={family}
              onChange={(e) => setFamily(e.target.value)}
              placeholder="e.g. qwen3, llama, mistral"
              className={inputClass}
            />
          </Field>
          <Field
            label="Modality"
            hint="Applies to every instance of this model — it describes the weights, not a process."
          >
            <select
              value={modality}
              onChange={(e) => setModality(e.target.value as Modality)}
              className={inputClass}
            >
              {MODALITIES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </Field>

          {runningWarning && (
            <p className="rounded-lg border border-border bg-background px-3 py-2 text-xs text-text-muted">
              {model.instance_ids.length === 1 ? "1 instance" : `${model.instance_ids.length} instances`}{" "}
              already exist. Restart them after saving — the flags a backend starts with depend on
              this, so a running process keeps whatever it was launched as.
            </p>
          )}
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-background"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={updateCatalog.isPending || !dirty || !family.trim() || !name.trim()}
            className={cn(
              "rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {updateCatalog.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
