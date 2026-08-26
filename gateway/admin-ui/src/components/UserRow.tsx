import { Pencil, Power, RotateCw } from "lucide-react";
import { useState } from "react";
import { useDeactivateUser, useReactivateUser, useResetPassword, useRotateSecret } from "../api/users";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import type { Principal } from "../types/user";
import { ConfirmDialog } from "./ConfirmDialog";
import { UserStatusBadge } from "./UserStatusBadge";

const actionButtonClass =
  "rounded-md p-1.5 transition-colors disabled:cursor-not-allowed disabled:opacity-30";

export function UserRow({
  user,
  onEdit,
  onRevealCredential,
}: {
  user: Principal;
  onEdit: (user: Principal) => void;
  onRevealCredential: (clientId: string, secret: string, label: string) => void;
}) {
  const { showToast } = useToast();
  const [confirmDeactivate, setConfirmDeactivate] = useState(false);
  const deactivate = useDeactivateUser();
  const reactivate = useReactivateUser();
  const rotateSecret = useRotateSecret();
  const resetPassword = useResetPassword();
  const isBusy =
    deactivate.isPending || reactivate.isPending || rotateSecret.isPending || resetPassword.isPending;

  const runAction = (mutateAsync: () => Promise<unknown>, successMessage: string) => {
    mutateAsync().then(
      () => showToast(successMessage, "success"),
      (error: unknown) => showToast(getErrorMessage(error), "error"),
    );
  };

  const handleRotate = () => {
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

  return (
    <>
      <tr className="border-b border-border last:border-0">
        <td className="px-4 py-3 font-medium text-text">{user.client_name}</td>
        <td className="px-4 py-3 text-text-muted">
          {user.auth_method === "oauth2" ? "Client ID / Secret" : "Email / Password"}
        </td>
        <td className="px-4 py-3 text-text-muted">
          {user.auth_method === "oauth2" ? user.client_id : user.email}
        </td>
        <td className="px-4 py-3 text-text-muted capitalize">{user.role}</td>
        <td className="px-4 py-3 text-text-muted">
          <div className="flex max-w-xs flex-wrap gap-1">
            {user.allowed_scopes.map((scope) => (
              <span key={scope} className="rounded-full bg-background px-2 py-0.5 text-xs">
                {scope}
              </span>
            ))}
          </div>
        </td>
        <td className="px-4 py-3">
          <UserStatusBadge isActive={user.is_active} />
        </td>
        <td className="px-4 py-3">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              title={user.auth_method === "oauth2" ? "Rotate secret" : "Reset password"}
              aria-label={`Rotate credential for ${user.client_name}`}
              disabled={isBusy || !user.is_active}
              onClick={handleRotate}
              className={cn(actionButtonClass, "text-blue-600 hover:bg-blue-50")}
            >
              <RotateCw size={16} />
            </button>
            <button
              type="button"
              title="Edit"
              aria-label={`Edit ${user.client_name}`}
              disabled={isBusy}
              onClick={() => onEdit(user)}
              className={cn(actionButtonClass, "text-text-muted hover:bg-background")}
            >
              <Pencil size={16} />
            </button>
            <button
              type="button"
              title={user.is_active ? "Deactivate" : "Reactivate"}
              aria-label={`${user.is_active ? "Deactivate" : "Reactivate"} ${user.client_name}`}
              disabled={isBusy}
              onClick={() =>
                user.is_active
                  ? setConfirmDeactivate(true)
                  : runAction(() => reactivate.mutateAsync(user.client_id), `${user.client_name} reactivated`)
              }
              className={cn(
                actionButtonClass,
                user.is_active ? "text-red-600 hover:bg-red-50" : "text-green-600 hover:bg-green-50",
              )}
            >
              <Power size={16} />
            </button>
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
    </>
  );
}
