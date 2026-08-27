import { Plus } from "lucide-react";
import { useState } from "react";
import { useNodeRegistry } from "../api/nodes";
import { CreateNodeModal } from "../components/CreateNodeModal";
import { NodeTable } from "../components/NodeTable";
import { Sidebar } from "../components/Sidebar";
import type { Node } from "../types/node";

const EMPTY_NODES: Node[] = [];

type ModalState = { mode: "create" } | { mode: "edit"; node: Node } | null;

export default function Nodes() {
  const nodesQuery = useNodeRegistry();
  const [modal, setModal] = useState<ModalState>(null);

  const nodes = nodesQuery.data ?? EMPTY_NODES;

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="flex-1 px-8 py-8">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-text">Nodes</h1>
          <button
            type="button"
            onClick={() => setModal({ mode: "create" })}
            className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            <Plus size={16} />
            Register node
          </button>
        </div>

        <div className="mt-6">
          {nodesQuery.isLoading ? (
            <div className="rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
              Loading nodes…
            </div>
          ) : (
            <NodeTable nodes={nodes} onEdit={(node) => setModal({ mode: "edit", node })} />
          )}
        </div>
      </main>

      <CreateNodeModal
        key={modal?.mode === "edit" ? modal.node.id : "create"}
        open={modal !== null}
        editing={modal?.mode === "edit" ? modal.node : null}
        onClose={() => setModal(null)}
      />
    </div>
  );
}
