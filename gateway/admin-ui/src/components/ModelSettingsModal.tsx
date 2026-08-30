import { Settings, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useModelsConfig, useUpdateModelsConfig } from "../api/models";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import type { ModelsConfig } from "../types/models";

interface ModelSettingsModalProps {
  open: boolean;
  node: string;
  onClose: () => void;
}

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none";

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block text-sm text-text">
      <span className="mb-1 block text-xs font-medium text-text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-text-muted">{hint}</span>}
    </label>
  );
}

/** Holds the editable form fields, mounted only once the current config has
 * loaded — so its local state can initialize straight from `initial` with no
 * effect needed to sync in server data after the fact. */
function ModelSettingsForm({
  node,
  initial,
  onClose,
}: {
  node: string;
  initial: ModelsConfig;
  onClose: () => void;
}) {
  const { showToast } = useToast();
  const updateConfig = useUpdateModelsConfig();

  const [downloadsDir, setDownloadsDir] = useState(initial.downloads_dir);
  const [hfTokenEnv, setHfTokenEnv] = useState(initial.hf_token_env);
  const [caBundle, setCaBundle] = useState(initial.ca_bundle);

  function handleSave() {
    updateConfig.mutate(
      { node, data: { downloads_dir: downloadsDir, hf_token_env: hfTokenEnv, ca_bundle: caBundle } },
      {
        onSuccess: () => {
          showToast("Settings updated for this session", "success");
          onClose();
        },
        onError: (e) => showToast(getErrorMessage(e), "error"),
      },
    );
  }

  return (
    <>
      <div className="space-y-4">
        <Field
          label="Downloads directory"
          hint="Where new downloads are saved on this node. Existing files aren't moved."
        >
          <input
            value={downloadsDir}
            onChange={(e) => setDownloadsDir(e.target.value)}
            className={inputClass}
          />
        </Field>
        <Field
          label="Hugging Face token env var"
          hint="Name of the env var holding an HF access token, if one is set on this node."
        >
          <input
            value={hfTokenEnv}
            onChange={(e) => setHfTokenEnv(e.target.value)}
            className={inputClass}
          />
        </Field>
        <Field
          label="CA bundle path"
          hint="Only needed behind a corporate TLS-inspecting proxy. Leave blank otherwise."
        >
          <input
            value={caBundle}
            onChange={(e) => setCaBundle(e.target.value)}
            placeholder="(none)"
            className={inputClass}
          />
        </Field>
        <p className="rounded-lg bg-background p-3 text-xs text-text-muted">
          Changes apply immediately to this manager session. They aren't written to manager.toml,
          so a restart reverts to the file's own values — edit it directly for a change that
          persists.
        </p>
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
          disabled={updateConfig.isPending}
          className={cn(
            "rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90",
            "disabled:cursor-not-allowed disabled:opacity-50",
          )}
        >
          {updateConfig.isPending ? "Saving…" : "Save"}
        </button>
      </div>
    </>
  );
}

export function ModelSettingsModal({ open, node, onClose }: ModelSettingsModalProps) {
  const configQuery = useModelsConfig(node);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-8">
      <div className="w-full max-w-md rounded-xl bg-surface p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-text">
            <Settings size={18} />
            Model settings — {node}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={18} className="text-text-muted" />
          </button>
        </div>

        {configQuery.isLoading || !configQuery.data ? (
          <p className="text-sm text-text-muted">Loading…</p>
        ) : (
          <ModelSettingsForm node={node} initial={configQuery.data} onClose={onClose} />
        )}
      </div>
    </div>,
    document.body,
  );
}
