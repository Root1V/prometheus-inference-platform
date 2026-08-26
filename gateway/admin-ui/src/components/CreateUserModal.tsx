import { X } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useCreateUser, useUpdateUser } from "../api/users";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import type {
  AuthMethod,
  CreatePrincipalRequest,
  CreatePrincipalResponse,
  Principal,
  PrincipalRole,
} from "../types/user";
import { ScopePicker } from "./ScopePicker";

interface CreateUserModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: (response: CreatePrincipalResponse) => void;
  /** When set, the modal edits this existing principal (PATCH) instead of
   * creating a new one. auth_method/role/email aren't editable in place —
   * changing how a principal authenticates isn't a field edit, it's a
   * re-registration, out of scope here (mirrors RegisterModelModal's
   * node/id-read-only-when-editing pattern). */
  editing?: Principal | null;
}

const ROLES: PrincipalRole[] = ["admin", "cognitive", "agent", "app"];

interface FormState {
  client_name: string;
  role: PrincipalRole;
  allowed_scopes: string[];
  label: string;
  auth_method: AuthMethod;
  email: string;
  password: string;
}

function initialState(): FormState {
  return {
    client_name: "",
    role: "app",
    allowed_scopes: [],
    label: "",
    auth_method: "password",
    email: "",
    password: "",
  };
}

function stateFromUser(user: Principal): FormState {
  return {
    client_name: user.client_name,
    role: user.role as PrincipalRole,
    allowed_scopes: user.allowed_scopes,
    label: user.label ?? "",
    auth_method: user.auth_method,
    email: user.email ?? "",
    password: "",
  };
}

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none";

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: ReactNode;
}) {
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

export function CreateUserModal({ open, onClose, onCreated, editing = null }: CreateUserModalProps) {
  const { showToast } = useToast();
  const createUser = useCreateUser();
  const updateUser = useUpdateUser();
  const isEditing = editing !== null;
  const [form, setForm] = useState<FormState>(() => (editing ? stateFromUser(editing) : initialState()));

  if (!open) return null;

  const isPending = createUser.isPending || updateUser.isPending;
  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();

    if (form.allowed_scopes.length === 0) {
      showToast("Select at least one scope", "error");
      return;
    }

    if (isEditing) {
      updateUser.mutate(
        {
          clientId: editing.client_id,
          data: {
            client_name: form.client_name,
            label: form.label || null,
            allowed_scopes: form.allowed_scopes,
          },
        },
        {
          onSuccess: () => {
            showToast(`${form.client_name} updated`, "success");
            onClose();
          },
          onError: (error) => showToast(getErrorMessage(error), "error"),
        },
      );
      return;
    }

    const body: CreatePrincipalRequest = {
      client_name: form.client_name,
      role: form.role,
      allowed_scopes: form.allowed_scopes,
      auth_method: form.auth_method,
      ...(form.label ? { label: form.label } : {}),
      ...(form.auth_method === "password" ? { email: form.email, password: form.password } : {}),
    };

    createUser.mutate(body, {
      onSuccess: (data) => {
        showToast(`${data.client_name} created`, "success");
        setForm(initialState());
        onClose();
        onCreated(data);
      },
      onError: (error) => showToast(getErrorMessage(error), "error"),
    });
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-8">
      <div className="max-h-full w-full max-w-lg overflow-y-auto rounded-xl bg-surface p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text">
            {isEditing ? `Edit ${editing.client_name}` : "Create user"}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={18} className="text-text-muted" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          {!isEditing && (
            <div className="flex gap-2 rounded-lg border border-border p-1">
              {(["password", "oauth2"] as AuthMethod[]).map((method) => (
                <button
                  key={method}
                  type="button"
                  onClick={() => update("auth_method", method)}
                  className={cn(
                    "flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                    form.auth_method === method
                      ? "bg-primary text-primary-foreground"
                      : "text-text-muted hover:bg-background",
                  )}
                >
                  {method === "password" ? "Email + Password" : "Client ID + Secret"}
                </button>
              ))}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <Field label="Name" required>
              <input
                value={form.client_name}
                onChange={(e) => update("client_name", e.target.value)}
                required
                className={inputClass}
              />
            </Field>
            <Field label="Label">
              <input value={form.label} onChange={(e) => update("label", e.target.value)} className={inputClass} />
            </Field>
            {!isEditing && (
              <Field label="Role" required>
                <select
                  value={form.role}
                  onChange={(e) => update("role", e.target.value as PrincipalRole)}
                  className={inputClass}
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            {!isEditing && form.auth_method === "password" && (
              <>
                <Field label="Email" required>
                  <input
                    type="email"
                    value={form.email}
                    onChange={(e) => update("email", e.target.value)}
                    required
                    className={inputClass}
                  />
                </Field>
                <Field label="Password" required>
                  <input
                    type="password"
                    minLength={8}
                    value={form.password}
                    onChange={(e) => update("password", e.target.value)}
                    required
                    className={inputClass}
                  />
                </Field>
              </>
            )}
          </div>

          <Field label="Scopes" required>
            <ScopePicker value={form.allowed_scopes} onChange={(scopes) => update("allowed_scopes", scopes)} />
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
              {isPending ? "Saving…" : isEditing ? "Save changes" : "Create"}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}
