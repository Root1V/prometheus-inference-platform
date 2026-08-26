import { Pencil, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { useCheckNode, useDeleteNode } from "../api/nodes";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";
import type { Node } from "../types/node";
import { ConfirmDialog } from "./ConfirmDialog";
import { UserStatusBadge } from "./UserStatusBadge";

const actionButtonClass =
  "rounded-md p-1.5 transition-colors disabled:cursor-not-allowed disabled:opacity-30";

export function NodeRow({ node, onEdit }: { node: Node; onEdit: (node: Node) => void }) {
  const { showToast } = useToast();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const remove = useDeleteNode();
  const check = useCheckNode();

  return (
    <>
      <tr className="border-b border-border last:border-0">
        <td className="px-4 py-3 font-medium text-text">{node.name}</td>
        <td className="px-4 py-3 text-text-muted">{node.manager_url}</td>
        <td className="px-4 py-3 text-text-muted capitalize">{node.node_type}</td>
        <td className="px-4 py-3 text-text-muted">{node.tag ?? "—"}</td>
        <td className="px-4 py-3">
          <UserStatusBadge isActive={node.is_active} />
        </td>
        <td className="px-4 py-3">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              title="Re-check connectivity"
              aria-label={`Re-check connectivity for ${node.name}`}
              disabled={check.isPending}
              onClick={() =>
                check.mutate(node.id, {
                  onSuccess: (updated) =>
                    showToast(
                      `${node.name} is ${updated.is_active ? "reachable" : "unreachable"}`,
                      updated.is_active ? "success" : "error",
                    ),
                  onError: (error) => showToast(getErrorMessage(error), "error"),
                })
              }
              className={cn(actionButtonClass, "text-text-muted hover:bg-background")}
            >
              <RefreshCw size={16} className={check.isPending ? "animate-spin" : undefined} />
            </button>
            <button
              type="button"
              title="Edit"
              aria-label={`Edit ${node.name}`}
              disabled={remove.isPending}
              onClick={() => onEdit(node)}
              className={cn(actionButtonClass, "text-text-muted hover:bg-background")}
            >
              <Pencil size={16} />
            </button>
            <button
              type="button"
              title="Delete"
              aria-label={`Delete ${node.name}`}
              disabled={remove.isPending}
              onClick={() => setConfirmDelete(true)}
              className={cn(actionButtonClass, "text-red-600 hover:bg-red-50")}
            >
              <Trash2 size={16} />
            </button>
          </div>
        </td>
      </tr>
      <ConfirmDialog
        open={confirmDelete}
        title={`Delete ${node.name}?`}
        description="Instances registered on this node will no longer be reachable from the dashboard. This cannot be undone."
        confirmLabel="Delete"
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => {
          setConfirmDelete(false);
          remove.mutate(node.id, {
            onSuccess: () => showToast(`${node.name} deleted`, "success"),
            onError: (error) => showToast(getErrorMessage(error), "error"),
          });
        }}
      />
    </>
  );
}
