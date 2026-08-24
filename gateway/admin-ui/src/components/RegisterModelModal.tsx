import { X } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useRegisterModel } from "../api/instances";
import { useToast } from "../context/ToastContext";
import { getErrorMessage } from "../lib/errors";
import type { Backend, Modality, RegisterModelRequest } from "../types/instance";

interface RegisterModelModalProps {
  open: boolean;
  nodes: string[];
  onClose: () => void;
}

const BACKENDS: Backend[] = ["llama_cpp", "mlx", "vllm", "sglang"];
const MODALITIES: Modality[] = ["text", "vision", "embedding"];

interface FormState {
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
  hf_filename: string;
  hf_sha256: string;
}

function initialState(defaultNode: string): FormState {
  return {
    node: defaultNode,
    id: "",
    port: "",
    path: "",
    context_length: "",
    family: "",
    quantization: "",
    backend: "llama_cpp",
    modality: "text",
    mmproj_path: "",
    discovery: false,
    hf_repo: "",
    hf_filename: "",
    hf_sha256: "",
  };
}

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none";

function Field({ label, required, children }: { label: string; required?: boolean; children: ReactNode }) {
  return (
    <label className="block text-sm text-text">
      <span className="mb-1 block text-xs font-medium text-text-muted">
        {label}
        {required && <span className="text-red-500"> *</span>}
      </span>
      {children}
    </label>
  );
}

export function RegisterModelModal({ open, nodes, onClose }: RegisterModelModalProps) {
  const { showToast } = useToast();
  const registerModel = useRegisterModel();
  const [form, setForm] = useState<FormState>(() => initialState(""));

  if (!open) return null;

  // Nodes load asynchronously after this component mounts — fall back to the
  // first available node until the operator picks one explicitly. Derived
  // during render rather than synced via an effect (no extra render needed).
  const selectedNode = form.node || nodes[0] || "";

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!selectedNode) {
      showToast("Select a node", "error");
      return;
    }

    const body: RegisterModelRequest = {
      id: form.id,
      port: Number(form.port),
      backend: form.backend,
      modality: form.modality,
      discovery: form.discovery,
    };
    if (form.path) body.path = form.path;
    if (form.context_length) body.context_length = Number(form.context_length);
    if (form.family) body.family = form.family;
    if (form.quantization) body.quantization = form.quantization;
    if (form.modality === "vision" && form.mmproj_path) body.mmproj_path = form.mmproj_path;
    if (form.hf_repo) body.hf_repo = form.hf_repo;
    if (form.hf_filename) body.hf_filename = form.hf_filename;
    if (form.hf_sha256) body.hf_sha256 = form.hf_sha256;

    registerModel.mutate(
      { node: selectedNode, data: body },
      {
        onSuccess: () => {
          showToast(`${body.id} registered`, "success");
          setForm(initialState(""));
          onClose();
        },
        onError: (error) => showToast(getErrorMessage(error), "error"),
      },
    );
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-8">
      <div className="max-h-full w-full max-w-lg overflow-y-auto rounded-xl bg-surface p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text">Register model</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={18} className="text-text-muted" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Node" required>
              <select
                value={selectedNode}
                onChange={(e) => update("node", e.target.value)}
                required
                className={inputClass}
              >
                <option value="" disabled>
                  Select node
                </option>
                {nodes.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="ID" required>
              <input
                value={form.id}
                onChange={(e) => update("id", e.target.value)}
                required
                pattern="^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$"
                title="Lowercase letters, digits, hyphens, underscores"
                className={inputClass}
              />
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
              <select
                value={form.backend}
                onChange={(e) => update("backend", e.target.value as Backend)}
                className={inputClass}
              >
                {BACKENDS.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Modality">
              <select
                value={form.modality}
                onChange={(e) => update("modality", e.target.value as Modality)}
                className={inputClass}
              >
                {MODALITIES.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Family">
              <input value={form.family} onChange={(e) => update("family", e.target.value)} className={inputClass} />
            </Field>
            <Field label="Quantization">
              <input
                value={form.quantization}
                onChange={(e) => update("quantization", e.target.value)}
                className={inputClass}
              />
            </Field>
            <Field label="Path (local .gguf)">
              <input value={form.path} onChange={(e) => update("path", e.target.value)} className={inputClass} />
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
            <Field label="HF repo">
              <input
                value={form.hf_repo}
                onChange={(e) => update("hf_repo", e.target.value)}
                className={inputClass}
              />
            </Field>
            <Field label="HF filename">
              <input
                value={form.hf_filename}
                onChange={(e) => update("hf_filename", e.target.value)}
                className={inputClass}
              />
            </Field>
            <Field label="HF sha256">
              <input
                value={form.hf_sha256}
                onChange={(e) => update("hf_sha256", e.target.value)}
                className={inputClass}
              />
            </Field>
          </div>
          <label className="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              checked={form.discovery}
              onChange={(e) => update("discovery", e.target.checked)}
            />
            Expose via gateway discovery
          </label>
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
              disabled={registerModel.isPending}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              {registerModel.isPending ? "Registering…" : "Register"}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}
