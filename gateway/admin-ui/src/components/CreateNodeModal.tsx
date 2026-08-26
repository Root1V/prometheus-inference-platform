import { X } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useCreateNode, useUpdateNode } from "../api/nodes";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
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

interface FormState {
  name: string;
  manager_url: string;
  node_type: NodeType;
  tag: string;
}

function initialState(): FormState {
  return { name: "", manager_url: "", node_type: "mac", tag: "" };
}

function stateFromNode(node: Node): FormState {
  return {
    name: node.name,
    manager_url: node.manager_url,
    node_type: node.node_type,
    tag: node.tag ?? "",
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

    if (isEditing) {
      updateNode.mutate(
        {
          id: editing.id,
          data: { manager_url: form.manager_url, node_type: form.node_type, tag: form.tag || null },
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
