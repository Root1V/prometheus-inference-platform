import { Boxes, CircleCheck, CirclePause, Plus, Server } from "lucide-react";
import { useMemo, useState } from "react";
import { useInstances, useNodes } from "../api/instances";
import { InstanceTable } from "../components/InstanceTable";
import { RegisterModelModal } from "../components/RegisterModelModal";
import { Sidebar } from "../components/Sidebar";
import { StatCard } from "../components/StatCard";
import { WarningBanner } from "../components/WarningBanner";
import type { InstanceEntry } from "../types/instance";

// Stable references so they don't retrigger the useMemo below on every poll
// while data is still loading.
const EMPTY_INSTANCES: InstanceEntry[] = [];
const EMPTY_NODES: string[] = [];

type ModalState = { mode: "create" } | { mode: "edit"; instance: InstanceEntry } | null;

export default function Dashboard() {
  const instancesQuery = useInstances();
  const nodesQuery = useNodes();
  const [modal, setModal] = useState<ModalState>(null);

  const instances = instancesQuery.data?.instances ?? EMPTY_INSTANCES;
  const unreachableNodes = instancesQuery.data?.unreachable_nodes ?? EMPTY_NODES;
  const nodes = nodesQuery.data ?? EMPTY_NODES;
  const downloadedModels = useMemo(() => instances.filter((i) => i.downloaded), [instances]);

  const stats = useMemo(
    () => ({
      total: instances.length,
      running: instances.filter((i) => i.state === "ready").length,
      stopped: instances.filter((i) => i.state === "stopped").length,
      nodes: nodes.length,
    }),
    [instances, nodes],
  );

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="min-w-0 flex-1 px-8 py-8">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-text">Instances</h1>
          <button
            type="button"
            onClick={() => setModal({ mode: "create" })}
            className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            <Plus size={16} />
            Register model
          </button>
        </div>

        {unreachableNodes.length > 0 && (
          <div className="mt-4">
            <WarningBanner nodes={unreachableNodes} />
          </div>
        )}

        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Total instances" value={stats.total} icon={Boxes} />
          <StatCard label="Running" value={stats.running} icon={CircleCheck} />
          <StatCard label="Stopped" value={stats.stopped} icon={CirclePause} />
          <StatCard label="Nodes configured" value={stats.nodes} icon={Server} />
        </div>

        <div className="mt-6">
          {instancesQuery.isLoading ? (
            <div className="rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
              Loading instances…
            </div>
          ) : (
            <InstanceTable
              instances={instances}
              onEdit={(instance) => setModal({ mode: "edit", instance })}
            />
          )}
        </div>
      </main>

      <RegisterModelModal
        key={modal?.mode === "edit" ? modal.instance.id : "create"}
        open={modal !== null}
        nodes={nodes}
        editing={modal?.mode === "edit" ? modal.instance : null}
        downloadedModels={downloadedModels}
        onClose={() => setModal(null)}
      />
    </div>
  );
}
