import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  Boxes,
  Coins,
  Gauge,
  HardDrive,
  Timer,
  Trophy,
  Users as UsersIcon,
} from "lucide-react";
import { Link } from "react-router-dom";
import { useMetrics } from "../api/metrics";
import { useInstances } from "../api/instances";
import { useNodeRegistry } from "../api/nodes";
import { useUsage } from "../api/usage";
import { useUsers } from "../api/users";
import { AttentionTable, type AttentionEntry } from "../components/AttentionTable";
import { BudgetAlertBanner } from "../components/BudgetAlertBanner";
import { Sidebar } from "../components/Sidebar";
import { StatCard } from "../components/StatCard";
import { formatUptime, formatUsdCost } from "../lib/format";

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
  const usageQuery = useUsage();

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

  /**
   * PRM-205: a rate needs its denominator before it means anything.
   *
   * This dashboard read `33.3%` while the gateway had served **three
   * requests** — one failure out of three, after a restart. Colouring that red
   * is a false alarm, and false alarms are how a colour stops being read. Below
   * a usable sample the card stays neutral and says how many requests it is
   * talking about, so the number can be dismissed rather than believed.
   */
  const MEANINGFUL_SAMPLE = 20;
  const requestsSeen = inference?.requests_total ?? 0;
  const errorFraction =
    inference && requestsSeen > 0 ? inference.errors_total / requestsSeen : 0;
  const errorTone =
    requestsSeen < MEANINGFUL_SAMPLE
      ? "neutral"
      : errorFraction >= 0.1
        ? "bad"
        : errorFraction >= 0.01
          ? "warn"
          : "good";
  const errorReason =
    requestsSeen < MEANINGFUL_SAMPLE
      ? `Only ${requestsSeen} request${requestsSeen === 1 ? "" : "s"} since the gateway started — too few to read a rate from.`
      : errorTone === "bad"
        ? `${inference?.errors_total} of ${requestsSeen} requests failed.`
        : errorTone === "warn"
          ? `${inference?.errors_total} of ${requestsSeen} requests failed.`
          : `${requestsSeen} requests, essentially none failing.`;

  const backends = metricsQuery.data?.backends ?? {};
  const attentionEntries: AttentionEntry[] = instances
    .map((instance) => ({ instance, circuitState: backends[instance.id]?.circuit_state }))
    .filter(
      ({ instance, circuitState }) =>
        instance.state === "error" || circuitState === "open" || circuitState === "half-open",
    )
    .sort((a, b) => attentionScore(b) - attentionScore(a));

  // RM-34: today's usage & cost, aggregated from RM-32/33's per-client/per-model data.
  const usageEntries = usageQuery.data?.data ?? [];
  const tokensToday = usageEntries.reduce((sum, e) => sum + e.total_tokens, 0);
  const hasAnyCost = usageEntries.some((e) => e.estimated_cost_usd !== null);
  const spendToday = usageEntries.reduce((sum, e) => sum + (e.estimated_cost_usd ?? 0), 0);
  const modelTotals = new Map<string, number>();
  for (const e of usageEntries) {
    for (const m of e.by_model) {
      modelTotals.set(m.model_id, (modelTotals.get(m.model_id) ?? 0) + m.total_tokens);
    }
  }
  const topModel = [...modelTotals.entries()].sort((a, b) => b[1] - a[1])[0];

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="min-w-0 flex-1 px-8 py-8">
        <h1 className="text-2xl font-semibold text-text">Overview</h1>

        {metricsQuery.data?.dependencies?.redis.reachable === false && (
          <div className="mt-4 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-500/10 dark:text-red-300">
            <AlertTriangle size={18} className="mt-0.5 shrink-0" />
            <span>
              {/* RM-79: not dismissible. The platform is answering 401 to
                  everything, and /health still reads green — this is the only
                  place that says so. */}
              <strong>Redis is unreachable.</strong>{" "}
              {metricsQuery.data.dependencies.redis.impact}
            </span>
          </div>
        )}

        <div className="mt-4">
          <BudgetAlertBanner />
        </div>

        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Nodes"
            value={`${activeNodes} / ${nodes.length}`}
            sub="active"
            // Also unambiguous: a configured node that is not active is capacity
            // the platform believes it has and does not.
            tone={nodes.length === 0 ? "neutral" : activeNodes < nodes.length ? "bad" : "good"}
            toneReason={
              nodes.length === 0
                ? undefined
                : activeNodes < nodes.length
                  ? `${nodes.length - activeNodes} configured node${nodes.length - activeNodes === 1 ? " is" : "s are"} not active.`
                  : "Every configured node is active."
            }
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
          <StatCard
            label="Error rate"
            value={inference ? errorRate : "—"}
            sub={requestsSeen > 0 ? `of ${requestsSeen.toLocaleString()} requests` : undefined}
            tone={errorTone}
            toneReason={errorReason}
            icon={AlertTriangle}
          />
          <StatCard
            label="Latency (p50)"
            value={inference ? `${inference.latency_p50_ms} ms` : "—"}
            sub={inference ? `p95 ${inference.latency_p95_ms}ms · p99 ${inference.latency_p99_ms}ms` : undefined}
            toneReason="Deliberately uncoloured: what counts as slow depends entirely on the model and the request. A threshold here would invent alarms."
            icon={Gauge}
          />
          <StatCard
            label="Circuits open"
            value={openCircuits}
            // RM-73: these are backends, not models. With replicas the old
            // "of N models" was simply wrong — two instances of one model read
            // as two models.
            sub={`of ${backendEntries.length} instance${backendEntries.length === 1 ? "" : "s"}${halfOpenCircuits > 0 ? ` · ${halfOpenCircuits} half-open` : ""}`}
            // Unambiguous, unlike latency: a tripped breaker means the gateway
            // has stopped sending traffic to a backend. There is no reading of
            // that which is fine.
            tone={openCircuits > 0 ? "bad" : halfOpenCircuits > 0 ? "warn" : "good"}
            toneReason={
              openCircuits > 0
                ? `${openCircuits} backend${openCircuits === 1 ? " is" : "s are"} being skipped because its circuit tripped.`
                : halfOpenCircuits > 0
                  ? `${halfOpenCircuits} backend${halfOpenCircuits === 1 ? " is" : "s are"} being probed after a trip.`
                  : "Every backend is taking traffic."
            }
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

        <h2 className="mt-10 text-sm font-medium uppercase tracking-wide text-text-muted">
          Usage &amp; cost
        </h2>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatCard
            label="Tokens today"
            value={usageQuery.data ? tokensToday.toLocaleString() : "—"}
            sub={usageQuery.data ? `${usageEntries.length} client${usageEntries.length === 1 ? "" : "s"}` : undefined}
            icon={Activity}
          />
          <StatCard
            label="Est. spend today"
            value={usageQuery.data ? (hasAnyCost ? formatUsdCost(spendToday) : "—") : "—"}
            sub={usageQuery.data && !hasAnyCost ? "No pricing configured" : undefined}
            icon={Coins}
          />
          <StatCard
            label="Top model"
            value={topModel ? topModel[0] : "—"}
            sub={topModel ? `${topModel[1].toLocaleString()} tokens today` : undefined}
            icon={Trophy}
          />
        </div>

        <div className="mt-8 flex flex-wrap gap-2">
          <Link to="/instances" className={linkChipClass}>
            → Instances
          </Link>
          <Link to="/nodes" className={linkChipClass}>
            → Nodes
          </Link>
          <Link to="/usage" className={linkChipClass}>
            → Usage
          </Link>
          <Link to="/users" className={linkChipClass}>
            → Users
          </Link>
        </div>
      </main>
    </div>
  );
}
