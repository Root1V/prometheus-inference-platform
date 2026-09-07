import { AlertTriangle, X } from "lucide-react";
import { useState } from "react";

/** Dismissible, non-blocking warning listing manager nodes that didn't respond. */
export function WarningBanner({ nodes }: { nodes: string[] }) {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed || nodes.length === 0) return null;

  return (
    <div className="flex items-start justify-between gap-4 rounded-lg border border-yellow-200 bg-yellow-50 px-4 py-3 text-sm text-yellow-800 dark:border-yellow-900 dark:bg-yellow-500/10 dark:text-yellow-300">
      <div className="flex items-start gap-2">
        <AlertTriangle size={18} className="mt-0.5 shrink-0" />
        <span>
          Unreachable nodes (their instances may be missing below): <strong>{nodes.join(", ")}</strong>
        </span>
      </div>
      <button type="button" onClick={() => setDismissed(true)} aria-label="Dismiss warning">
        <X size={16} />
      </button>
    </div>
  );
}
