import { X } from "lucide-react";
import { formatBytes } from "../lib/format";
import type { InstanceEntry } from "../types/instance";
import { Badge, ModalityBadge } from "./Badge";
import { ModelCardView } from "./ModelCardView";
import { StatusBadge } from "./StatusBadge";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-text-muted">{label}</dt>
      <dd className="mt-0.5 text-sm text-text">{value}</dd>
    </div>
  );
}

/** Right-side preview shown when a row in the Library tab's downloaded-models
 * table is clicked — metadata plus the Hugging Face model card, so an
 * operator can check what a model is without leaving the page. */
export function ModelPreviewPanel({
  model,
  node,
  onClose,
}: {
  model: InstanceEntry;
  node: string;
  onClose: () => void;
}) {
  return (
    <aside className="flex w-96 shrink-0 flex-col rounded-xl border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border p-4">
        <h2 className="truncate text-sm font-semibold text-text">{model.id}</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close preview"
          className="text-text-muted hover:text-text"
        >
          <X size={18} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <div className="mb-2 flex flex-wrap gap-2">
          <ModalityBadge modality={model.modality} />
          <Badge>{model.quantization || "unknown quant"}</Badge>
          <StatusBadge state={model.state} message={model.error_message} />
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-3">
          <Stat label="Family" value={model.family || "—"} />
          <Stat label="Backend" value={model.backend} />
          <Stat
            label="Context"
            value={model.context_length > 0 ? model.context_length.toLocaleString() : "—"}
          />
          <Stat label="Size on disk" value={formatBytes(model.file_size_bytes)} />
          <Stat label="Port" value={String(model.port)} />
          <Stat label="Node" value={model.node} />
        </dl>

        <p className="mt-4 truncate text-xs text-text-muted" title={model.hf_repo}>
          {model.hf_repo}
        </p>

        <div className="mt-4 border-t border-border pt-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
            Model card
          </h3>
          {model.hf_repo ? (
            <ModelCardView node={node} repoId={model.hf_repo} />
          ) : (
            <p className="text-sm text-text-muted">No source repository recorded for this model.</p>
          )}
        </div>
      </div>
    </aside>
  );
}
