import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";

/** The full client id with its own copy button — PRM-280. The table shortens it
 * to fit a row; the edit and reveal dialogs are where someone goes to fetch it,
 * so there it is whole. An integration needs the id as much as the secret, and
 * the reveal dialog used to offer only the secret. */
export function ClientIdField({ clientId }: { clientId: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1400);
    return () => clearTimeout(t);
  }, [copied]);

  return (
    <div>
      <div className="text-xs font-medium text-text-muted">Client ID</div>
      <div className="mt-1 flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-2">
        <span className="min-w-0 flex-1 break-all font-mono text-sm text-text">{clientId}</span>
        <button
          type="button"
          onClick={() => {
            navigator.clipboard?.writeText(clientId).then(
              () => setCopied(true),
              () => setCopied(false),
            );
          }}
          aria-label="Copy client ID"
          className="shrink-0 rounded p-1 text-text-muted hover:bg-surface hover:text-text"
        >
          {copied ? <Check size={14} className="text-green-600" /> : <Copy size={14} />}
        </button>
      </div>
    </div>
  );
}
