import { Boxes, HardDrive, Timer, Users as UsersIcon } from "lucide-react";
import { Link } from "react-router-dom";
import { useMetrics } from "../api/metrics";
import { useInstances } from "../api/instances";
import { useNodeRegistry } from "../api/nodes";
import { useUsers } from "../api/users";
import { Sidebar } from "../components/Sidebar";
import { StatCard } from "../components/StatCard";
import { formatUptime } from "../lib/format";

const linkChipClass =
  "rounded-full border border-border bg-surface px-3 py-1.5 text-xs font-medium text-text-muted hover:bg-background hover:text-text";

export default function Overview() {
  const nodesQuery = useNodeRegistry();
  const instancesQuery = useInstances();
  const usersQuery = useUsers();
  const metricsQuery = useMetrics();

  const nodes = nodesQuery.data ?? [];
  const instances = instancesQuery.data?.instances ?? [];
  const users = usersQuery.data ?? [];

  const activeNodes = nodes.filter((n) => n.is_active).length;
  const runningInstances = instances.filter((i) => i.state === "ready").length;
  const stoppedInstances = instances.filter((i) => i.state === "stopped").length;
  const activeUsers = users.filter((u) => u.is_active).length;

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="flex-1 px-8 py-8">
        <h1 className="text-2xl font-semibold text-text">Overview</h1>

        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Nodes"
            value={`${activeNodes} / ${nodes.length}`}
            sub="active"
            icon={HardDrive}
          />
          <StatCard
            label="Instances"
            value={instances.length}
            sub={`${runningInstances} running · ${stoppedInstances} stopped`}
            icon={Boxes}
          />
          <StatCard label="Users" value={activeUsers} sub={`of ${users.length} active`} icon={UsersIcon} />
          <StatCard
            label="Gateway uptime"
            value={metricsQuery.data ? formatUptime(metricsQuery.data.uptime_seconds) : "—"}
            icon={Timer}
          />
        </div>

        <div className="mt-8 flex flex-wrap gap-2">
          <Link to="/instances" className={linkChipClass}>
            → Instances
          </Link>
          <Link to="/nodes" className={linkChipClass}>
            → Nodes
          </Link>
          <Link to="/users" className={linkChipClass}>
            → Users
          </Link>
        </div>
      </main>
    </div>
  );
}
