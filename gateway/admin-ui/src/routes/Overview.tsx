import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  Boxes,
  Gauge,
  HardDrive,
  Timer,
  Users as UsersIcon,
} from "lucide-react";
import { Link } from "react-router-dom";
import { useMetrics } from "../api/metrics";
import { useInstances } from "../api/instances";
import { useNodeRegistry } from "../api/nodes";
import { useUsers } from "../api/users";
import { AttentionTable, type AttentionEntry } from "../components/AttentionTable";
import { Sidebar } from "../components/Sidebar";
import { StatCard } from "../components/StatCard";
import { formatUptime } from "../lib/format";

/** Higher = more urgent. An actual crash outranks a tripped circuit. */
function attentionScore(entry: AttentionEntry): number {
  return (entry.instance.state === "error" ? 2 : 0) + (entry.circuitState === "open" ? 2 : entry.circuitState === "half-open" ? 1 : 0);
}

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

  const inference = metricsQuery.data?.inference;
  const backendEntries = Object.values(metricsQuery.data?.backends ?? {});
  const openCircuits = backendEntries.filter((b) => b.circuit_state === "open").length;
  const halfOpenCircuits = backendEntries.filter((b) => b.circuit_state === "half-open").length;
  const errorRate =
    inference && inference.requests_total > 0
      ? `${((inference.errors_total / inference.requests_total) * 100).toFixed(1)}%`
      : "0.0%";

  const backends = metricsQuery.data?.backends ?? {};
  const attentionEntries: AttentionEntry[] = instances
    .map((instance) => ({ instance, circuitState: backends[instance.id]?.circuit_state }))
    .filter(
      ({ instance, circuitState }) =>
        instance.state === "error" || circuitState === "open" || circuitState === "half-open",
    )
    .sort((a, b) => attentionScore(b) - attentionScore(a));

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

        <h2 className="mt-10 text-sm font-medium uppercase tracking-wide text-text-muted">
          Request health
        </h2>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Requests"
            value={inference?.requests_active ?? "—"}
            sub={inference ? `active now · ${inference.requests_total} total` : undefined}
            icon={Activity}
          />
          <StatCard label="Error rate" value={inference ? errorRate : "—"} icon={AlertTriangle} />
          <StatCard
            label="Latency (p50)"
            value={inference ? `${inference.latency_p50_ms} ms` : "—"}
            sub={inference ? `p95 ${inference.latency_p95_ms}ms · p99 ${inference.latency_p99_ms}ms` : undefined}
            icon={Gauge}
          />
          <StatCard
            label="Circuits open"
            value={openCircuits}
            sub={`of ${backendEntries.length} models${halfOpenCircuits > 0 ? ` · ${halfOpenCircuits} half-open` : ""}`}
            icon={AlertOctagon}
          />
        </div>
        <p className="mt-2 text-xs text-text-muted">
          Counters are process-memory only — they reset when the gateway restarts, and reflect the
          current snapshot rather than a historical trend.
        </p>

        <h2 className="mt-10 text-sm font-medium uppercase tracking-wide text-text-muted">
          Needs attention
        </h2>
        <div className="mt-3">
          <AttentionTable entries={attentionEntries} />
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
