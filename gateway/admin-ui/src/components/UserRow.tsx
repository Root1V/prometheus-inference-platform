import {
  Check,
  Copy,
  KeyRound,
  Mail,
  MoreHorizontal,
  Pencil,
  Power,
  Receipt,
  RotateCw,
  Trash2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  useDeactivateUser,
  useDeleteUser,
  useReactivateUser,
  useResetPassword,
  useRotateSecret,
} from "../api/users";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import type { Principal } from "../types/user";
import { ConfirmDialog } from "./ConfirmDialog";
import { ScopeSummary } from "./ScopeSummary";
import { UserStatusBadge } from "./UserStatusBadge";

const MODEL_SCOPE_PREFIX = "model:";

/**
 * PRM-201. How many model chips are shown before the rest fold behind a count.
 *
 * The number is not arbitrary: the widest grant on this deployment is eleven
 * models, which stacked to roughly a 400px row and put two users on a 1080p
 * screen. Three is what fits on one line at the narrowest column width this
 * table reaches, so every row is the same height and the table can be scanned
 * down the Name column instead of read.
 */
const MODELS_SHOWN = 3;

/** An hour or more is long enough that a leaked token stays useful; the
 * platform's own default for an agent is ten minutes. Flagged rather than
 * refused — a human-operated admin session has a reason to be longer. */
const LONG_LIVED_SECONDS = 3600;

const isLongLived = (seconds: number) => seconds >= LONG_LIVED_SECONDS;

function formatTtl(seconds: number): string {
  if (seconds >= 3600) {
    const hours = seconds / 3600;
    return `${Number.isInteger(hours) ? hours : hours.toFixed(1)}h`;
  }
  if (seconds >= 60) return `${Math.round(seconds / 60)} min`;
  return `${seconds}s`;
}

/** Age, not a date. "30d" answers "is this leftover?"; a timestamp makes you
 * do the subtraction. The exact value is on hover. */
function formatAge(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days < 1) return "today";
  if (days < 30) return `${days}d old`;
  const months = Math.floor(days / 30);
  return months < 12 ? `${months}mo old` : `${Math.floor(months / 12)}y old`;
}

function Chip({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className="inline-block max-w-[11rem] truncate rounded-full bg-background px-2 py-0.5 text-xs text-text-muted"
    >
      {children}
    </span>
  );
}

/** The identifier, copyable. It is a UUID: shown whole it costs 200px of every
 * row, and the only thing anyone does with it is paste it somewhere. */
function Identifier({ value, isOauth }: { value: string; isOauth: boolean }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1400);
    return () => clearTimeout(t);
  }, [copied]);

  const short = isOauth && value.length > 13 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;

  return (
    <div className="flex items-center gap-1.5">
      {isOauth ? (
        <KeyRound size={13} className="shrink-0 text-text-muted" aria-hidden />
      ) : (
        <Mail size={13} className="shrink-0 text-text-muted" aria-hidden />
      )}
      <span className="truncate font-mono text-xs text-text-muted" title={value}>
        {short}
      </span>
      <button
        type="button"
        onClick={() => {
          navigator.clipboard?.writeText(value).then(
            () => setCopied(true),
            () => setCopied(false),
          );
        }}
        aria-label={`Copy identifier ${value}`}
        className="rounded p-1 text-text-muted opacity-0 transition hover:bg-background hover:text-text focus:opacity-100 group-hover/row:opacity-100"
      >
        {copied ? <Check size={12} className="text-green-600" /> : <Copy size={12} />}
      </button>
    </div>
  );
}

export function UserRow({
  user,
  highlight,
  onEdit,
  onBilling,
  onRevealCredential,
}: {
  user: Principal;
  /** Lowercased search term, so a matching model chip can be surfaced even when
   * it would otherwise be folded away — searching for a model and seeing a row
   * that does not visibly contain it reads as a broken filter. */
  highlight?: string;
  onEdit: (user: Principal) => void;
  onBilling: (user: Principal) => void;
  onRevealCredential: (clientId: string, secret: string, label: string) => void;
}) {
  const { showToast } = useToast();
  const [confirmDeactivate, setConfirmDeactivate] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showAllModels, setShowAllModels] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const deactivate = useDeactivateUser();
  const deleteUser = useDeleteUser();
  const reactivate = useReactivateUser();
  const rotateSecret = useRotateSecret();
  const resetPassword = useResetPassword();
  const isBusy =
    deactivate.isPending ||
    deleteUser.isPending ||
    reactivate.isPending ||
    rotateSecret.isPending ||
    resetPassword.isPending;

  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [menuOpen]);

  const runAction = (mutateAsync: () => Promise<unknown>, successMessage: string) => {
    mutateAsync().then(
      () => showToast(successMessage, "success"),
      (error: unknown) => showToast(getErrorMessage(error), "error"),
    );
  };

  const platformScopes = user.allowed_scopes.filter((s) => !s.startsWith(MODEL_SCOPE_PREFIX));
  const allModels = user.allowed_scopes
    .filter((s) => s.startsWith(MODEL_SCOPE_PREFIX))
    .map((s) => s.slice(MODEL_SCOPE_PREFIX.length))
    .sort();

  // A model matching the search is pulled to the front, so the reason this row
  // survived the filter is the first thing visible.
  const ordered = highlight
    ? [...allModels].sort((a, b) => {
        const am = a.toLowerCase().includes(highlight) ? 0 : 1;
        const bm = b.toLowerCase().includes(highlight) ? 0 : 1;
        return am - bm;
      })
    : allModels;
  const visible = showAllModels ? ordered : ordered.slice(0, MODELS_SHOWN);
  const hidden = ordered.length - visible.length;

  const handleRotate = () => {
    setMenuOpen(false);
    if (user.auth_method === "oauth2") {
      rotateSecret.mutateAsync(user.client_id).then(
        (data) => onRevealCredential(user.client_id, data.client_secret, "Client secret"),
        (error: unknown) => showToast(getErrorMessage(error), "error"),
      );
    } else {
      resetPassword.mutateAsync(user.client_id).then(
        (data) => onRevealCredential(user.client_id, data.password, "Password"),
        (error: unknown) => showToast(getErrorMessage(error), "error"),
      );
    }
  };

  const menuItem =
    "flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40";

  return (
    <>
      <tr className="group/row border-b border-border align-middle last:border-0 hover:bg-background/60">
        <td className="min-w-[11rem] px-4 py-3">
          {/* A minimum, because with one search result the table shrinks to its
              content and a name like `rm46-verify` broke across two lines. */}
          <div className="font-medium text-text">{user.client_name}</div>
          {/*
            PRM-201: `label` was already in the API response and the UI threw it
            away. It is set on 11 of the 14 credentials here and carries the one
            thing the name does not — "Video Vigilancia", "Device Control",
            "Learning Presentation". A credential list whose first question is
            "what is this for?" should not make you open an edit dialog to find
            out.
          */}
          <div className="mt-0.5 truncate text-xs text-text-muted" title={user.label ?? undefined}>
            <span className="capitalize">{user.role}</span>
            {user.label && (
              <>
                <span aria-hidden> · </span>
                {user.label}
              </>
            )}
          </div>
        </td>

        <td className="max-w-[16rem] px-4 py-3">
          <Identifier
            value={user.auth_method === "oauth2" ? user.client_id : (user.email ?? "")}
            isOauth={user.auth_method === "oauth2"}
          />
          <div className="mt-0.5 flex items-center gap-1.5 text-xs text-text-muted">
            {/*
              Token lifetime, because it is a security property that was
              editable here and invisible here. Four different values are in use
              on this deployment (5 min to 3 h) and the longest sits on the one
              credential holding `admin:write` — which is exactly the pairing
              worth seeing without opening anything.
            */}
            <span
              title={`Issued tokens live for ${user.token_ttl_seconds} seconds`}
              className={cn(isLongLived(user.token_ttl_seconds) && "text-amber-700 dark:text-amber-400")}
            >
              {formatTtl(user.token_ttl_seconds)} token
            </span>
            <span aria-hidden>·</span>
            <span title={`Created ${new Date(user.created_at).toLocaleString()}`}>
              {formatAge(user.created_at)}
            </span>
          </div>
        </td>

        <td className="px-4 py-3">
          <ScopeSummary scopes={platformScopes} />
        </td>

        <td className="px-4 py-3">
          {allModels.length === 0 ? (
            <span className="text-xs text-text-muted">No model access</span>
          ) : (
            <div className="flex flex-wrap items-center gap-1">
              {visible.map((id) => (
                <Chip key={id} title={id}>
                  {highlight && id.toLowerCase().includes(highlight) ? (
                    <mark className="bg-primary/25 text-text">{id}</mark>
                  ) : (
                    id
                  )}
                </Chip>
              ))}
              {hidden > 0 && (
                <button
                  type="button"
                  onClick={() => setShowAllModels(true)}
                  className="rounded-full border border-border px-2 py-0.5 text-xs text-text-muted transition-colors hover:border-primary hover:text-text"
                  aria-label={`Show all ${allModels.length} models for ${user.client_name}`}
                >
                  +{hidden}
                </button>
              )}
              {showAllModels && allModels.length > MODELS_SHOWN && (
                <button
                  type="button"
                  onClick={() => setShowAllModels(false)}
                  className="rounded-full px-2 py-0.5 text-xs text-text-muted underline-offset-2 hover:underline"
                >
                  less
                </button>
              )}
            </div>
          )}
        </td>

        <td className="px-4 py-3">
          <UserStatusBadge isActive={user.is_active} />
        </td>

        <td className="px-4 py-3">
          {/*
            Two buttons, then a menu. Before PRM-201 there were five bare icons
            in a row, with Deactivate and Delete adjacent and distinguishable
            only by glyph — the two that cannot be undone sat one pixel-perfect
            click apart. Edit is the common case and stays; everything that
            changes a credential or ends one is behind a deliberate second step.
          */}
          <div className="flex items-center justify-end gap-1">
            <button
              type="button"
              title="Edit"
              aria-label={`Edit ${user.client_name}`}
              disabled={isBusy}
              onClick={() => onEdit(user)}
              className="rounded-md p-1.5 text-text-muted transition-colors hover:bg-background hover:text-text disabled:opacity-30"
            >
              <Pencil size={16} />
            </button>
            <div className="relative" ref={menuRef}>
              <button
                type="button"
                aria-label={`More actions for ${user.client_name}`}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                disabled={isBusy}
                onClick={() => setMenuOpen((o) => !o)}
                className={cn(
                  "rounded-md p-1.5 text-text-muted transition-colors hover:bg-background hover:text-text disabled:opacity-30",
                  menuOpen && "bg-background text-text",
                )}
              >
                <MoreHorizontal size={16} />
              </button>
              {menuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 z-20 mt-1 w-56 overflow-hidden rounded-lg border border-border bg-surface py-1 shadow-lg"
                >
                  <button
                    type="button"
                    role="menuitem"
                    disabled={!user.is_active}
                    onClick={handleRotate}
                    className={cn(menuItem, "text-text hover:bg-background")}
                  >
                    <RotateCw size={15} className="text-text-muted" />
                    {user.auth_method === "oauth2" ? "Rotate secret" : "Reset password"}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      onBilling(user);
                    }}
                    className={cn(menuItem, "text-text hover:bg-background")}
                  >
                    <Receipt size={15} className="text-text-muted" />
                    Billing settings
                  </button>
                  <div className="my-1 border-t border-border" />
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      if (user.is_active) setConfirmDeactivate(true);
                      else
                        runAction(
                          () => reactivate.mutateAsync(user.client_id),
                          `${user.client_name} reactivated`,
                        );
                    }}
                    className={cn(
                      menuItem,
                      user.is_active
                        ? "text-amber-700 hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-950/40"
                        : "text-green-700 hover:bg-green-50 dark:text-green-400 dark:hover:bg-green-950/40",
                    )}
                  >
                    <Power size={15} />
                    {user.is_active ? "Deactivate" : "Reactivate"}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      setConfirmDelete(true);
                    }}
                    className={cn(
                      menuItem,
                      "text-red-700 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40",
                    )}
                  >
                    <Trash2 size={15} />
                    Delete permanently
                  </button>
                </div>
              )}
            </div>
          </div>
        </td>
      </tr>

      <ConfirmDialog
        open={confirmDeactivate}
        title={`Deactivate ${user.client_name}?`}
        description="This immediately revokes access — any tokens already issued are rejected too. Can be reactivated later."
        confirmLabel="Deactivate"
        onCancel={() => setConfirmDeactivate(false)}
        onConfirm={() => {
          setConfirmDeactivate(false);
          runAction(() => deactivate.mutateAsync(user.client_id), `${user.client_name} deactivated`);
        }}
      />
      <ConfirmDialog
        open={confirmDelete}
        title={`Permanently delete ${user.client_name}?`}
        description="Unlike Deactivate, this cannot be undone — the user record is removed entirely and any tokens already issued are rejected immediately."
        confirmLabel="Delete permanently"
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => {
          setConfirmDelete(false);
          runAction(() => deleteUser.mutateAsync(user.client_id), `${user.client_name} deleted`);
        }}
      />
    </>
  );
}
