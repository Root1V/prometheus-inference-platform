import { X } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useInstanceDefaults, useRegisterModel, useUpdateModel } from "../api/instances";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import { ENGINE_LABELS, enginesAvailableOn } from "../lib/engines";
import type {
  Backend,
  InstanceEntry,
  Modality,
  RegisterModelRequest,
  UpdateModelRequest,
} from "../types/instance";
import type { ModelCatalogEntry } from "../types/models";
import type { Node } from "../types/node";

interface RegisterModelModalProps {
  open: boolean;
  nodes: Node[];
  onClose: () => void;
  /** When set, the modal edits this existing instance (PATCH) instead of
   * registering a new one (POST). Node and ID become read-only — moving a
   * model to a different node or renaming it isn't a field edit, it's a
   * re-registration, out of scope here. */
  editing?: InstanceEntry | null;
  /** RM-51: catalog entries to populate the "Model" picker when creating a
   * new instance — path/family/quantization/mmproj_path are derived from the
   * selection instead of typed by hand, since those describe the downloaded
   * file, not a per-instance choice. Submitted as `model_id`, letting
   * manager-api pull those fields from the catalog server-side rather than
   * copying them into the request body. Ignored while editing. */
  downloadedModels?: ModelCatalogEntry[];
}


interface FormState {
  slug: string;
  node: string;
  id: string;
  port: string;
  path: string;
  context_length: string;
  family: string;
  quantization: string;
  backend: Backend;
  modality: Modality;
  mmproj_path: string;
  discovery: boolean;
  hf_repo: string;
  hf_sha256: string;
}

function stateFromInstance(instance: InstanceEntry): FormState {
  return {
    slug: instance.model_slug,
    node: instance.node,
    id: instance.id,
    port: String(instance.port),
    path: instance.path,
    context_length: String(instance.context_length),
    family: instance.family,
    quantization: instance.quantization,
    backend: instance.backend,
    modality: instance.modality,
    mmproj_path: instance.mmproj_path,
    discovery: instance.discovery,
    hf_repo: instance.hf_repo,
    hf_sha256: instance.hf_sha256,
  };
}

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none";

function Field({
  label,
  required,
  hint,
  children,
}: {
  label: string;
  required?: boolean;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block text-sm text-text">
      <span className="mb-1 block text-xs font-medium text-text-muted">
        {label}
        {required && <span className="text-red-500"> *</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-text-muted">{hint}</span>}
    </label>
  );
}

function SummaryRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-text-muted">{label}</dt>
      <dd className="min-w-0 break-all font-mono text-text">{children}</dd>
    </>
  );
}

/** A catalog entry is unique per node, not globally — the same id can be
 * downloaded on two nodes — so the picker keys on both. */
const sourceKey = (m: ModelCatalogEntry) => `${m.node}::${m.id}`;

export function RegisterModelModal({
  open,
  nodes,
  onClose,
  editing = null,
  downloadedModels = [],
}: RegisterModelModalProps) {
  if (!open) return null;
  return editing ? (
    <EditInstanceForm instance={editing} nodes={nodes} onClose={onClose} />
  ) : (
    <NewInstanceForm nodes={nodes} downloadedModels={downloadedModels} onClose={onClose} />
  );
}

function ModalShell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-8">
      <div className="max-h-full w-full max-w-lg overflow-y-auto rounded-xl bg-surface p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={18} className="text-text-muted" />
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}

function FormButtons({
  onClose,
  isPending,
  label,
  disabled,
}: {
  onClose: () => void;
  isPending: boolean;
  label: string;
  disabled?: boolean;
}) {
  return (
    <div className="flex justify-end gap-3 pt-2">
      <button
        type="button"
        onClick={onClose}
        className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-background"
      >
        Cancel
      </button>
      <button
        type="submit"
        disabled={isPending || disabled}
        className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
      >
        {isPending ? "Saving…" : label}
      </button>
    </div>
  );
}

/**
 * PRM-281: picking the model is the whole form. Everything else either
 * describes the file (path, family, quantization, modality — from the catalog)
 * or has an answer the node can work out (id, port, context, engine — from
 * `instance-defaults`). The three that are genuinely a choice stay one click
 * away under "Adjust"; left blank, the node's answer is what gets registered.
 */
function NewInstanceForm({
  nodes,
  downloadedModels,
  onClose,
}: {
  nodes: Node[];
  downloadedModels: ModelCatalogEntry[];
  onClose: () => void;
}) {
  const { showToast } = useToast();
  const registerModel = useRegisterModel();
  const [selectedKey, setSelectedKey] = useState("");
  const [adjusting, setAdjusting] = useState(false);
  const [port, setPort] = useState("");
  const [contextLength, setContextLength] = useState("");
  const [backendChoice, setBackendChoice] = useState<Backend | "">("");
  // PRM-281: on by default. An instance nobody can discover receives no
  // traffic, which is almost never what registering one is for.
  const [discovery, setDiscovery] = useState(true);

  const source = downloadedModels.find((m) => sourceKey(m) === selectedKey) ?? null;
  const node = source?.node ?? "";
  const defaultsQuery = useInstanceDefaults(node, source?.id ?? "");
  const defaults = defaultsQuery.data ?? null;

  // PRM-133: the engines this node declared, narrowed to the ones this build
  // can launch — the operator's pick, else the engine an existing instance of
  // this model runs on, else the first the node offers.
  const availableEngines = enginesAvailableOn(nodes.find((n) => n.name === node)?.engines);
  const preferred = backendChoice || defaults?.backend;
  const engine =
    preferred && availableEngines.includes(preferred) ? preferred : (availableEngines[0] ?? null);
  // Without a preview (a node not yet running PRM-281, or an error) nothing
  // is asked for anyway: whatever is left blank, the node chooses on save.
  const defaultsFailed = defaultsQuery.isError;

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!source) {
      showToast("Select a model", "error");
      return;
    }
    if (engine === null) {
      showToast(`${node} has no inference engine declared.`, "error");
      return;
    }
    // No `id`: the node derives it when the row is written, so two people
    // registering at once cannot both take the one this form displayed. A
    // blank port is left out for the same reason — the node picks one then.
    const chosenPort = Number(port) || defaults?.port;
    const body: RegisterModelRequest = {
      model_id: source.id,
      ...(chosenPort ? { port: chosenPort } : {}),
      backend: engine,
      // PRM-110: not modality — the server takes it from the catalog entry
      // this instance references.
      discovery,
    };
    if (contextLength) body.context_length = Number(contextLength);

    registerModel.mutate(
      { node, data: body },
      {
        onSuccess: (created) => {
          showToast(`${created.id} registered on ${node}`, "success");
          onClose();
        },
        onError: (error) => showToast(getErrorMessage(error), "error"),
      },
    );
  };

  // A node without PRM-281 answers 404 here and still defaults a blank context
  // to 4096 on registration, so the copy must not promise the model's.
  const contextSummary = contextLength
    ? Number(contextLength).toLocaleString("en-US")
    : defaults
      ? `${defaults.context_length.toLocaleString("en-US")} ${
          defaults.context_from_model ? "(the model's)" : "(the model declares none)"
        }`
      : defaultsFailed
        ? "chosen on save"
        : "…";

  return (
    <ModalShell title="Register model" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Model" required>
          {downloadedModels.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border px-3 py-2 text-xs text-text-muted">
              No downloaded models yet — download one from the Models page first.
            </p>
          ) : (
            <select
              value={selectedKey}
              onChange={(e) => {
                setSelectedKey(e.target.value);
                // A different model on a different node: the old overrides
                // were answers to a different question.
                setPort("");
                setContextLength("");
                setBackendChoice("");
              }}
              required
              className={inputClass}
            >
              <option value="" disabled>
                Select a downloaded model
              </option>
              {downloadedModels.map((m) => (
                <option key={sourceKey(m)} value={sourceKey(m)}>
                  {m.id} · {m.node} — {m.family || "?"} · {m.quantization || "?"}
                </option>
              ))}
            </select>
          )}
        </Field>

        {source && (
          <div className="rounded-lg border border-border bg-background p-3">
            {defaultsFailed && (
              <p className="mb-2 text-xs text-text-muted">
                No preview from {node}: it fills these in when you register.
              </p>
            )}
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
              <SummaryRow label="ID">{defaults?.id ?? (defaultsFailed ? "chosen on save" : "…")}</SummaryRow>
              <SummaryRow label="Node">{node}</SummaryRow>
              <SummaryRow label="Port">
                {port || defaults?.port || (defaultsFailed ? "chosen on save" : "…")}
              </SummaryRow>
              <SummaryRow label="Context">{contextSummary}</SummaryRow>
              <SummaryRow label="Engine">{engine ? ENGINE_LABELS[engine] : "none declared"}</SummaryRow>
              <SummaryRow label="Modality">{source.modality}</SummaryRow>
              <SummaryRow label="Quantization">{source.quantization || "—"}</SummaryRow>
              <SummaryRow label="File">{source.path || "—"}</SummaryRow>
            </dl>
            <button
              type="button"
              onClick={() => setAdjusting((v) => !v)}
              aria-expanded={adjusting}
              className="mt-3 text-xs font-medium text-primary hover:underline"
            >
              {adjusting ? "Hide port, context and engine" : "Adjust port, context or engine"}
            </button>
          </div>
        )}

        {source && adjusting && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Port" hint="Blank lets the node pick a free one.">
              <input
                type="number"
                min={1024}
                max={65535}
                value={port}
                placeholder={defaults ? String(defaults.port) : "auto"}
                onChange={(e) => setPort(e.target.value)}
                className={inputClass}
              />
            </Field>
            <Field
              label="Context length"
              hint={
                defaults
                  ? `Blank uses ${defaults.context_from_model ? "the model's" : "the default"} ${defaults.context_length.toLocaleString("en-US")}.`
                  : "Blank lets the node choose."
              }
            >
              <input
                type="number"
                min={1}
                value={contextLength}
                placeholder={defaults ? String(defaults.context_length) : undefined}
                onChange={(e) => setContextLength(e.target.value)}
                className={inputClass}
              />
            </Field>
            <Field label="Engine">
              {engine === null ? (
                <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-600">
                  {node} declared no inference engines. Add one on the Nodes page.
                </p>
              ) : (
                <select
                  value={engine}
                  onChange={(e) => setBackendChoice(e.target.value as Backend)}
                  className={inputClass}
                >
                  {availableEngines.map((b) => (
                    <option key={b} value={b}>
                      {ENGINE_LABELS[b]}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          </div>
        )}

        <p className="text-xs text-text-muted">
          Don't see the model you want? Download it from the{" "}
          <a href="#/models" className="text-primary hover:underline">
            Models
          </a>{" "}
          page first — it searches Hugging Face, shows the model card, and registers the finished
          download automatically.
        </p>
        <label className="flex items-center gap-2 text-sm text-text">
          <input
            type="checkbox"
            checked={discovery}
            onChange={(e) => setDiscovery(e.target.checked)}
          />
          Expose via gateway discovery
        </label>
        <FormButtons
          onClose={onClose}
          isPending={registerModel.isPending}
          label="Register"
          disabled={!source || engine === null || defaultsQuery.isLoading}
        />
      </form>
    </ModalShell>
  );
}

function EditInstanceForm({
  instance,
  nodes,
  onClose,
}: {
  instance: InstanceEntry;
  nodes: Node[];
  onClose: () => void;
}) {
  const { showToast } = useToast();
  const updateModel = useUpdateModel();
  const [form, setForm] = useState<FormState>(() => stateFromInstance(instance));

  // PRM-133: same rule as AddInstanceModal — the engines this node declared,
  // narrowed to the ones this build can launch. Both forms create an instance
  // on a node, so both have to obey the node's list; leaving one of them
  // unfiltered would be the "fixed at one call site" shape all over again.
  const availableEngines = enginesAvailableOn(
    nodes.find((n) => n.name === form.node)?.engines,
  );
  const engine = availableEngines.includes(form.backend) ? form.backend : (availableEngines[0] ?? null);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    // PRM-133: `engine`, not `form.backend`. The select renders `engine` — the
    // node's list may not contain what the form is holding — so submitting
    // `form.backend` would send an engine the operator was never shown, on a
    // node that declared it does not have it.
    if (engine === null) {
      showToast(`${form.node} has no inference engine declared.`, "error");
      return;
    }
    const body: UpdateModelRequest = {
      port: Number(form.port),
      backend: engine,
      // PRM-109: not modality — the server refuses it on an instance now.
      discovery: form.discovery,
      path: form.path,
      context_length: Number(form.context_length) || undefined,
      family: form.family,
      quantization: form.quantization,
      mmproj_path: form.modality === "vision" ? form.mmproj_path : "",
      hf_repo: form.hf_repo,
      hf_sha256: form.hf_sha256,
    };
    updateModel.mutate(
      { node: form.node, modelId: form.id, data: body },
      {
        onSuccess: () => {
          showToast(`${form.id} updated`, "success");
          onClose();
        },
        onError: (error) => showToast(getErrorMessage(error), "error"),
      },
    );
  };

  return (
    <ModalShell title={`Edit ${form.id}`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Node" required>
            <select value={form.node} disabled className={cn(inputClass, "cursor-not-allowed opacity-60")}>
              <option value={form.node}>{form.node}</option>
            </select>
          </Field>
          <Field label="ID" required>
            <input value={form.id} disabled className={cn(inputClass, "cursor-not-allowed opacity-60")} />
          </Field>
          {/* PRM-112: the public name moved to the Models page, alongside
              name, family and modality. It names the *model* — every instance
              of it answers to the same string — so editing it from one
              instance was editing the wrong thing, the same mistake PRM-109
              found with modality. Shown here read-only because it is the one
              thing a caller actually sends. */}
          <Field label="Public name" hint="Belongs to the model — set it from the Models page.">
            <input value={form.slug} readOnly disabled className={inputClass} />
          </Field>
          <Field label="Port" required>
            <input
              type="number"
              min={1024}
              max={65535}
              value={form.port}
              onChange={(e) => update("port", e.target.value)}
              required
              className={inputClass}
            />
          </Field>
          <Field label="Context length">
            <input
              type="number"
              placeholder="4096"
              value={form.context_length}
              onChange={(e) => update("context_length", e.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label="Backend">
            {engine === null ? (
              <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-600">
                {form.node} declared no inference engines. Add one on the Nodes page.
              </p>
            ) : (
              <select
                value={engine}
                onChange={(e) => update("backend", e.target.value as Backend)}
                className={inputClass}
              >
                {availableEngines.map((b) => (
                  <option key={b} value={b}>
                    {ENGINE_LABELS[b]}
                  </option>
                ))}
              </select>
            )}
          </Field>
          {/* PRM-110: read-only. PRM-109 left this editable while
              "registering", on the belief that the call created the model too
              — it does not: it sends `model_id` and creates an instance of a
              model already in the catalog. So there was always something to
              inherit from, and offering a dropdown here only ever let someone
              disagree with it. The pencil on the Models page is where
              modality changes. */}
          <Field
            label="Modality"
            hint="Belongs to the model — change it from the Models page so every instance agrees."
          >
            <input value={form.modality} readOnly disabled className={inputClass} />
          </Field>
          <Field label="Family">
            <input
              value={form.family}
              onChange={(e) => update("family", e.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label="Quantization">
            <input
              value={form.quantization}
              onChange={(e) => update("quantization", e.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label="Path (local .gguf)">
            <input
              value={form.path}
              onChange={(e) => update("path", e.target.value)}
              className={inputClass}
            />
          </Field>
          {form.modality === "vision" && (
            <Field label="mmproj path">
              <input
                value={form.mmproj_path}
                onChange={(e) => update("mmproj_path", e.target.value)}
                className={inputClass}
              />
            </Field>
          )}
        </div>
        <label className="flex items-center gap-2 text-sm text-text">
          <input
            type="checkbox"
            checked={form.discovery}
            onChange={(e) => update("discovery", e.target.checked)}
          />
          Expose via gateway discovery
        </label>
        <FormButtons onClose={onClose} isPending={updateModel.isPending} label="Save changes" />
      </form>
    </ModalShell>
  );
}
