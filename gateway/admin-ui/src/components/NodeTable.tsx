import type { Node } from "../types/node";
import { NodeRow } from "./NodeRow";

const COLUMNS = ["Name", "Manager URL", "Type", "Tag", "Actions"];

export function NodeTable({
  nodes,
  onEdit,
}: {
  nodes: Node[];
  onEdit: (node: Node) => void;
}) {
  if (nodes.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
        No nodes registered yet — click "Register node" to add one.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface">
      <table className="w-full min-w-[700px] text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
            {COLUMNS.map((col) => (
              <th key={col} className="px-4 py-3 font-medium">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {nodes.map((node) => (
            <NodeRow key={node.id} node={node} onEdit={onEdit} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
