import { useState } from "react";
import { createPortal } from "react-dom";
import { useGenerateShareLink } from "../api/users";
import { useToast } from "../context/ToastContext";
import { getErrorMessage } from "../lib/errors";

interface CredentialRevealDialogProps {
  open: boolean;
  clientId: string | null;
  secret: string | null;
  /** "Client secret" or "Password" — shown in the heading and copy. */
  label: string;
  onClose: () => void;
}

/**
 * Shown once after creating an oauth2/password user or rotating its
 * credential. Ported from the retired auth-service Jinja dashboard's
 * secret-reveal + share-link flow (RM-11) — here the plaintext is already in
 * memory from the mutation response, so generating a share link is a single
 * extra call instead of the old signed flash-cookie round trip.
 */
export function CredentialRevealDialog({
  open,
  clientId,
  secret,
  label,
  onClose,
}: CredentialRevealDialogProps) {
  const { showToast } = useToast();
  const generateShareLink = useGenerateShareLink();
  const [shareUrl, setShareUrl] = useState<string | null>(null);

  if (!open || !clientId || !secret) return null;

  const displayed = shareUrl ?? secret;

  const copy = async () => {
    await navigator.clipboard.writeText(displayed);
    showToast("Copied to clipboard", "success");
  };

  const handleClose = () => {
    setShareUrl(null);
    onClose();
  };

  const handleGenerateShareLink = () => {
    generateShareLink.mutate(
      { clientId, secret },
      {
        onSuccess: (data) => setShareUrl(data.share_url),
        onError: (error) => showToast(getErrorMessage(error), "error"),
      },
    );
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
      <div className="w-full max-w-md rounded-xl bg-surface p-6 shadow-lg">
        <h2 className="text-lg font-semibold text-text">{label} — shown once</h2>
        <p className="mt-2 text-sm text-text-muted">
          {shareUrl
            ? "Send this one-time link instead of the raw credential — it can only be opened once."
            : "Save this now — it can't be retrieved again. Copy it directly, or generate a one-time link to hand off instead."}
        </p>
        <div className="mt-4 break-all rounded-lg border border-border bg-background px-3 py-2 font-mono text-sm text-text">
          {displayed}
        </div>
        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={handleClose}
            className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-background"
          >
            Close
          </button>
          {!shareUrl && (
            <button
              type="button"
              onClick={handleGenerateShareLink}
              disabled={generateShareLink.isPending}
              className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-background disabled:opacity-50"
            >
              {generateShareLink.isPending ? "Generating…" : "Generate share link"}
            </button>
          )}
          <button
            type="button"
            onClick={copy}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            Copy
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
