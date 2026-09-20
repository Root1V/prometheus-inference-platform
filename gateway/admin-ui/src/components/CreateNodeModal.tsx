import { X } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useCreateNode, useUpdateNode } from "../api/nodes";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import { ENGINES, ENGINE_LABELS } from "../lib/engines";
import type { CreateNodeRequest, Node, NodeType } from "../types/node";

interface CreateNodeModalProps {
  open: boolean;
  onClose: () => void;
  /** When set, edits this existing node (PATCH) instead of creating a new one.
   * `name` is read-only when editing — renaming a node isn't a field edit,
   * it's a re-registration (mirrors CreateUserModal/RegisterModelModal). */
  editing?: Node | null;
}

const NODE_TYPES: NodeType[] = ["mac", "nvidia", "other"];

// RM-62 follow-up: mirrors db.py's DEFAULT_* constants — shown as
// placeholders only (the server applies the real defaults when a field is
// left blank at creation), so this repo's single source of truth stays the
// backend; these just tell the operator what they'll get.
const DEFAULT_AMORTIZATION_HINT = "0.3082";
const DEFAULT_ELECTRICITY_HINT = "0.0146";
const DEFAULT_MARGIN_HINT = "1.3";

interface FormState {
  name: string;
  manager_url: string;
  node_type: NodeType;
  tag: string;
  hardware_amortization: string;
  electricity: string;
  margin: string;
  /** PRM-133: `null` means the operator has not touched the list — the node
   * stays undeclared. Ticking or clearing any box makes it a declaration,
   * including the empty one. */
  engines: string[] | null;
}

function initialState(): FormState {
  return {
    name: "",
    manager_url: "",
    node_type: "mac",
    tag: "",
    hardware_amortization: "",
    electricity: "",
    margin: "",
    engines: null,
  };
}

function stateFromNode(node: Node): FormState {
  return {
    name: node.name,
    manager_url: node.manager_url,
    node_type: node.node_type,
    tag: node.tag ?? "",
    hardware_amortization: node.hardware_amortization_usd_per_hour.toString(),
    electricity: node.electricity_usd_per_hour.toString(),
    margin: node.price_margin_multiplier.toString(),
    engines: node.engines,
  };
}

/** Blank is valid (server applies its default); anything else must parse. */
function parseBlankableNumber(raw: string): number | undefined | "invalid" {
  if (raw.trim() === "") return undefined;
  const n = Number(raw);
  return Number.isNaN(n) ? "invalid" : n;
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

export function CreateNodeModal({ open, onClose, editing = null }: CreateNodeModalProps) {
  const { showToast } = useToast();
  const createNode = useCreateNode();
  const updateNode = useUpdateNode();
  const isEditing = editing !== null;
  const [form, setForm] = useState<FormState>(() => (editing ? stateFromNode(editing) : initialState()));

  if (!open) return null;

  const isPending = createNode.isPending || updateNode.isPending;
  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();

    const amortization = parseBlankableNumber(form.hardware_amortization);
    const electricity = parseBlankableNumber(form.electricity);
    const margin = parseBlankableNumber(form.margin);
    if (amortization === "invalid" || electricity === "invalid" || margin === "invalid") {
      showToast("Amortization, electricity, and margin must be numbers, or blank.", "error");
      return;
    }

    if (isEditing) {
      updateNode.mutate(
        {
          id: editing.id,
          data: {
            manager_url: form.manager_url,
            node_type: form.node_type,
            tag: form.tag || null,
            hardware_amortization_usd_per_hour: amortization,
            electricity_usd_per_hour: electricity,
            price_margin_multiplier: margin,
            // PRM-133: only when the operator declared something. Sending
            // `undefined` leaves the column alone; sending `[]` would turn
            // "never declared" into "has none" on every unrelated edit.
            ...(form.engines !== null ? { engines: form.engines } : {}),
          },
        },
        {
          onSuccess: () => {
            showToast(`${form.name} updated`, "success");
            onClose();
          },
          onError: (error) => showToast(getErrorMessage(error), "error"),
        },
      );
      return;
    }

    const body: CreateNodeRequest = {
      name: form.name,
      manager_url: form.manager_url,
      node_type: form.node_type,
      ...(form.tag ? { tag: form.tag } : {}),
      ...(amortization !== undefined ? { hardware_amortization_usd_per_hour: amortization } : {}),
      ...(electricity !== undefined ? { electricity_usd_per_hour: electricity } : {}),
      ...(margin !== undefined ? { price_margin_multiplier: margin } : {}),
      ...(form.engines !== null ? { engines: form.engines } : {}),
    };

    createNode.mutate(body, {
      onSuccess: () => {
        showToast(`${form.name} registered`, "success");
        setForm(initialState());
        onClose();
      },
      onError: (error) => showToast(getErrorMessage(error), "error"),
    });
  };

  const previewTotal =
    (Number(form.hardware_amortization) || 0) + (Number(form.electricity) || 0);

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-8">
      <div className="max-h-full w-full max-w-md overflow-y-auto rounded-xl bg-surface p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text">
            {isEditing ? `Edit ${editing.name}` : "Register node"}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={18} className="text-text-muted" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="Name" required>
            <input
              value={form.name}
              onChange={(e) => update("name", e.target.value)}
              required
              disabled={isEditing}
              placeholder="mac-studio-1"
              className={cn(inputClass, isEditing && "cursor-not-allowed opacity-60")}
            />
          </Field>
          <Field label="Manager URL" required>
            <input
              value={form.manager_url}
              onChange={(e) => update("manager_url", e.target.value)}
              required
              placeholder="http://192.168.1.50:8090"
              className={inputClass}
            />
          </Field>
          <Field label="Hardware type" required>
            <select
              value={form.node_type}
              onChange={(e) => update("node_type", e.target.value as NodeType)}
              className={inputClass}
            >
              {NODE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Tag">
            <input value={form.tag} onChange={(e) => update("tag", e.target.value)} className={inputClass} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Hardware amortization ($/hour)">
              <input
                value={form.hardware_amortization}
                onChange={(e) => update("hardware_amortization", e.target.value)}
                placeholder={`${DEFAULT_AMORTIZATION_HINT} (default)`}
                inputMode="decimal"
                className={inputClass}
              />
            </Field>
            <Field label="Electricity ($/hour)">
              <input
                value={form.electricity}
                onChange={(e) => update("electricity", e.target.value)}
                placeholder={`${DEFAULT_ELECTRICITY_HINT} (default)`}
                inputMode="decimal"
                className={inputClass}
              />
            </Field>
          </div>
          <p className="text-xs text-text-muted">
            Total: <span className="font-medium text-text">${previewTotal.toFixed(4)}/hour</span> —
            blank fields use the platform default shown above.
          </p>
          <Field label="Inference engines installed">
            <div className="space-y-1.5 rounded-lg border border-border bg-background px-3 py-2">
              {ENGINES.map((engine) => (
                <label key={engine} className="flex items-center gap-2 text-sm text-text">
                  <input
                    type="checkbox"
                    checked={form.engines?.includes(engine) ?? false}
                    onChange={(e) =>
                      update(
                        "engines",
                        e.target.checked
                          ? [...(form.engines ?? []), engine]
                          : (form.engines ?? []).filter((x) => x !== engine),
                      )
                    }
                    className="accent-primary"
                  />
                  {ENGINE_LABELS[engine]}
                </label>
              ))}
            </div>
          </Field>
          {form.engines === null ? (
            <p className="text-xs text-text-muted">
              Not declared. Creating an instance on this node will offer every engine, including
              ones it cannot run — which is what this list is for.
            </p>
          ) : form.engines.length === 0 ? (
            <p className="text-xs text-amber-500">
              Declared as having none. No instance can be created on this node until at least one
              engine is ticked.
            </p>
          ) : null}
          <Field label="Price-suggestion margin (×)">
            <input
              value={form.margin}
              onChange={(e) => update("margin", e.target.value)}
              placeholder={`${DEFAULT_MARGIN_HINT} (default)`}
              inputMode="decimal"
              className={inputClass}
            />
          </Field>
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
              disabled={isPending}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              {isPending ? "Saving…" : isEditing ? "Save changes" : "Register"}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}
