import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** When set, Confirm stays disabled until the operator types this exact
   * string into a text field — for destructive actions where a stray click
   * (as opposed to a considered decision) is a real risk, e.g. cascading a
   * delete across every running instance of a model. */
  requireTypedConfirmation?: string;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  onConfirm,
  onCancel,
  requireTypedConfirmation,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState("");
  // Reset the typed value each time the dialog opens (React's own
  // "adjusting state during render" pattern — see
  // react.dev/learn/you-might-not-need-an-effect), so a previous
  // confirmation doesn't silently carry over to a different row's dialog.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setTyped("");
  }

  if (!open) return null;

  const isLocked = requireTypedConfirmation !== undefined;
  const canConfirm = !isLocked || typed === requireTypedConfirmation;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
      <div className="w-full max-w-sm rounded-xl bg-surface p-6 shadow-lg">
        <h2 className="text-lg font-semibold text-text">{title}</h2>
        <div className="mt-2 text-sm text-text-muted">{description}</div>
        {isLocked && (
          <label className="mt-4 block text-sm text-text">
            <span className="mb-1 block text-xs font-medium text-text-muted">
              Type <span className="font-mono text-text">{requireTypedConfirmation}</span> to
              confirm
            </span>
            <input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none"
            />
          </label>
        )}
        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-background"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={!canConfirm}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-red-600"
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
