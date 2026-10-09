import {
  AlertOctagon,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CircleCheck,
  RefreshCw,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useInstances } from "../api/instances";
import { useNodeRegistry } from "../api/nodes";
import {
  useOverview,
  type AttentionItem,
  type Delta,
  type OverviewResponse,
} from "../api/overview";
import { useUsers } from "../api/users";
import {
  AttentionTable,
  type AttentionEntry,
} from "../components/AttentionTable";
import { Sidebar } from "../components/Sidebar";
import { formatUptime, formatUsdCost } from "../lib/format";

/**
 * Overview — rebuilt by PRM-246.
 *
 * What it replaced read `metrics_store`, which is the gateway's process
 * memory, so after a restart the platform's headline figures were
 * `0 active · 5 total` requests and a p95 equal to its p99 — five requests
 * since the deploy, printed as though they described a platform that had
 * served thousands. Beside them sat counts of things that exist (nodes,
 * instances, users), which answer "is the fleet where I left it" and not one
 * question anybody acts on.
 *
 * Everything that can come from the usage rows now does, so a deploy no
 * longer resets the dashboard. The order is the one the practice recommends
 * and the reason it recommends it: what needs attention, then the signals
 * with something to compare against, then the trend, then who and on what.
 */

const linkChipClass =
  "rounded-full border border-border bg-surface px-3 py-1.5 text-xs font-medium text-text-muted hover:bg-background hover:text-text";

/** Higher = more urgent. An actual crash outranks a tripped circuit. */
function attentionScore(entry: AttentionEntry): number {
  return (
    (entry.instance.state === "error" ? 2 : 0) +
    (entry.circuitState === "open"
      ? 2
      : entry.circuitState === "half-open"
        ? 1
        : 0)
  );
}

/**
 * A signal and what it was yesterday — PRM-246.
 *
 * A number on its own is not a signal; it becomes one next to the question
 * "more or less than before". `percent: null` is a real state and prints as
 * "no comparison" rather than as 0% — a first day of traffic has not stayed
 * flat, it has nothing behind it.
 *
 * **Only failures are coloured.** Traffic, tokens and cost have no good
 * direction: spending less can be a quiet week or a broken integration, and
 * the first version of this painted a 77.6% fall in cost red — alarming an
 * operator about a bill going down. Failures do have one, and a dashboard
 * that paints a rise in them green is worse than one with no colour at all.
 */
function Signal({
  label,
  delta,
  format = (n: number) => n.toLocaleString(),
  lowerIsBetter = false,
  sub,
}: {
  label: string;
  delta: Delta;
  format?: (n: number) => string;
  lowerIsBetter?: boolean;
  sub?: string;
}) {
  const percent = delta.percent;
  const rising = percent !== null && percent > 0;
  const flat = percent === null || Math.abs(percent) < 0.5;
  const tone = !lowerIsBetter
    ? "text-text-muted"
    : flat
      ? "text-text-muted"
      : rising
        ? "text-red-500"
        : "text-emerald-600 dark:text-emerald-400";
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
        {label}
      </p>
      <p className="mt-2 text-2xl font-semibold tabular-nums text-text">
        {format(delta.today)}
      </p>
      <p
        className={`mt-1 flex items-center gap-1 text-xs tabular-nums ${tone}`}
      >
        {percent === null ? (
          <span className="text-text-muted">
            nothing yesterday to compare with
          </span>
        ) : (
          <>
            {rising ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
            {Math.abs(percent)}%
            <span className="text-text-muted">
              vs {format(delta.yesterday)} yesterday
            </span>
          </>
        )}
      </p>
      {sub && <p className="mt-1 text-[11px] text-text-muted">{sub}</p>}
    </div>
  );
}

function Attention({ items }: { items: AttentionItem[] }) {
  if (items.length === 0) {
    return (
      <div className="mt-3 flex items-center gap-2 rounded-xl border border-border bg-surface px-4 py-3 text-sm text-text-muted">
        <CircleCheck
          size={16}
          className="text-emerald-600 dark:text-emerald-400"
        />
        Nothing is near a ceiling, over a cap, or refusing requests.
      </div>
    );
  }
  return (
    <div className="mt-3 space-y-2">
      {items.map((item, i) => (
        <Link
          key={`${item.kind}-${i}`}
          to={item.where.replace("/#", "")}
          className={
            "flex items-center gap-2 rounded-xl border px-4 py-3 text-sm hover:opacity-90 " +
            (item.severity === "critical"
              ? "border-red-500/30 bg-red-500/5 text-red-500"
              : "border-amber-500/30 bg-amber-500/5 text-amber-600 dark:text-amber-400")
          }
        >
          {item.severity === "critical" ? (
            <AlertOctagon size={16} />
          ) : (
            <AlertTriangle size={16} />
          )}
          <span className="font-medium">{item.what}</span>
        </Link>
      ))}
    </div>
  );
}

type Metric = "request_count" | "tokens" | "cost_usd";

const METRICS: { key: Metric; label: string; format: (n: number) => string }[] =
  [
    {
      key: "request_count",
      label: "Requests",
      format: (n) => n.toLocaleString(),
    },
    { key: "tokens", label: "Tokens", format: (n) => n.toLocaleString() },
    { key: "cost_usd", label: "Cost", format: (n) => formatUsdCost(n) },
  ];

function Trend({ data }: { data: OverviewResponse }) {
  const [metric, setMetric] = useState<Metric>("request_count");
  const chosen = METRICS.find((m) => m.key === metric)!;
  // Same series, three readings. Separate charts would be three answers to
  // "has this been growing", read one at a time, on a screen meant to be
  // taken in at a glance.
  return (
    <div className="mt-3 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-text-muted">
          Last {data.days} days, from the usage rows — a restart does not change
          this.
        </p>
        <div className="flex gap-1">
          {METRICS.map((m) => (
            <button
              key={m.key}
              type="button"
              onClick={() => setMetric(m.key)}
              className={
                "rounded-lg px-2.5 py-1 text-xs font-medium " +
                (metric === m.key
                  ? "bg-primary text-primary-foreground"
                  : "border border-border text-text-muted hover:bg-background")
              }
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-3 h-56">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={data.series}
            margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
          >
            <defs>
              <linearGradient id="overviewFill" x1="0" y1="0" x2="0" y2="1">
                <stop
                  offset="0%"
                  stopColor="var(--color-primary)"
                  stopOpacity={0.35}
                />
                <stop
                  offset="100%"
                  stopColor="var(--color-primary)"
                  stopOpacity={0.02}
                />
              </linearGradient>
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="var(--color-border)"
              vertical={false}
            />
            <XAxis
              dataKey="day"
              tick={{ fontSize: 11, fill: "var(--color-text-muted)" }}
              tickFormatter={(d: string) => d.slice(5)}
              stroke="var(--color-border)"
            />
            <YAxis
              tick={{ fontSize: 11, fill: "var(--color-text-muted)" }}
              stroke="var(--color-border)"
              width={56}
            />
            <Tooltip
              formatter={(value: unknown) =>
                chosen.format(
                  typeof value === "number" ? value : Number(value ?? 0),
                )
              }
              contentStyle={{
                background: "var(--color-surface)",
                border: "1px solid var(--color-border)",
                borderRadius: 8,
                fontSize: 12,
              }}
            />
            <Area
              type="monotone"
              dataKey={metric}
              stroke="var(--color-primary)"
              strokeWidth={2}
              fill="url(#overviewFill)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export default function Overview() {
  const overviewQuery = useOverview();
  const instancesQuery = useInstances();
  const nodesQuery = useNodeRegistry();
  const usersQuery = useUsers();

  const data = overviewQuery.data;
  const names = new Map(
    (usersQuery.data ?? []).map((u) => [
      u.client_id,
      u.label || u.client_name || u.client_id,
    ]),
  );

  // The fleet, kept and demoted to one line: counts of things that exist are
  // a glance, not a decision, and they were the headline.
  const instances = instancesQuery.data?.instances ?? [];
  const running = instances.filter((i) => i.state === "ready").length;
  const nodes = nodesQuery.data ?? [];
  const activeNodes = nodes.filter((n) => n.is_active).length;

  const unhealthy: AttentionEntry[] = instances
    .filter((i) => i.state === "error")
    .map((i) => ({ instance: i, circuitState: "closed" as const }))
    .sort((a, b) => attentionScore(b) - attentionScore(a));

  const errorsToday =
    (data?.signals.interrupted.today ?? 0) +
    (data?.signals.upstream_errors.today ?? 0);
  const requestsToday = data?.signals.requests.today ?? 0;

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="min-w-0 flex-1 px-8 py-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold text-text">Overview</h1>
          <div className="flex items-center gap-3 text-xs text-text-muted">
            <span className="tabular-nums">
              {activeNodes}/{nodes.length} nodes · {running} running · up{" "}
              {formatUptime(data?.fleet.uptime_seconds ?? 0)}
            </span>
            <button
              type="button"
              onClick={() => void overviewQuery.refetch()}
              disabled={overviewQuery.isFetching}
              className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw
                size={14}
                className={
                  overviewQuery.isFetching ? "animate-spin" : undefined
                }
              />
              Refresh
            </button>
          </div>
        </div>

        {overviewQuery.isError && (
          <p className="mt-4 rounded-xl border border-border bg-surface px-4 py-3 text-sm text-red-600">
            Could not read the platform figures.
          </p>
        )}

        {/* ── 1 · what needs attention ─────────────────────────────────── */}
        <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-text-muted">
          Needs attention
        </h2>
        <Attention items={data?.attention ?? []} />

        {/* ── 2 · the signals, each against yesterday ──────────────────── */}
        <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-text-muted">
          Today
        </h2>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {data ? (
            <>
              <Signal label="Requests" delta={data.signals.requests} />
              <Signal label="Tokens" delta={data.signals.tokens} />
              <Signal
                label="Cost"
                delta={data.signals.cost_usd}
                format={(n) => formatUsdCost(n)}
              />
              <Signal
                label="Failed or abandoned"
                delta={{
                  today: errorsToday,
                  yesterday:
                    data.signals.interrupted.yesterday +
                    data.signals.upstream_errors.yesterday,
                  percent: null,
                }}
                lowerIsBetter
                sub={
                  requestsToday
                    ? `${((errorsToday / requestsToday) * 100).toFixed(1)}% of today's requests · ${data.signals.upstream_errors.today} backend, ${data.signals.interrupted.today} abandoned`
                    : "nothing billed today yet"
                }
              />
            </>
          ) : (
            <div className="col-span-full rounded-xl border border-border bg-surface p-6 text-sm text-text-muted">
              Reading the usage rows…
            </div>
          )}
        </div>

        {/* ── 3 · the trend ────────────────────────────────────────────── */}
        <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-text-muted">
          Trend
        </h2>
        {data && <Trend data={data} />}

        {/* ── 4 · who, and on what ─────────────────────────────────────── */}
        {/* Two columns only from `xl`: at 1080px each table was ~280px wide
            against a 420px minimum, so the cost column fell off the right.
            A 13-inch laptop is not an edge case. */}
        <div className="mt-8 grid grid-cols-1 gap-4 xl:grid-cols-2">
          <div>
            <h2 className="text-sm font-medium uppercase tracking-wide text-text-muted">
              Models, last {data?.days ?? 14} days
            </h2>
            <div className="mt-3 overflow-x-auto rounded-xl border border-border bg-surface">
              <table className="w-full min-w-[420px] text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                    <th className="px-4 py-3 font-medium">Model</th>
                    <th className="px-4 py-3 text-right font-medium">
                      Requests
                    </th>
                    <th className="px-4 py-3 text-right font-medium">p95</th>
                    <th className="px-4 py-3 text-right font-medium">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.models ?? []).map((m) => (
                    <tr
                      key={m.model_id}
                      className="border-b border-border last:border-0"
                    >
                      <td className="px-4 py-3 font-mono text-xs text-text">
                        {m.model_id}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-text-muted">
                        {m.request_count.toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-text-muted">
                        {m.latency ? (
                          `${(m.latency.p95_ms / 1000).toFixed(1)}s`
                        ) : (
                          <span
                            className="text-[11px]"
                            title="No request of this model carries a duration — rows written before PRM-245 have none."
                          >
                            —
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-text-muted">
                        {formatUsdCost(m.cost_usd)}
                      </td>
                    </tr>
                  ))}
                  {(data?.models ?? []).length === 0 && (
                    <tr>
                      <td
                        colSpan={4}
                        className="p-8 text-center text-text-muted"
                      >
                        Nothing billed in this window.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div>
            <h2 className="text-sm font-medium uppercase tracking-wide text-text-muted">
              Consumers today
            </h2>
            <div className="mt-3 overflow-x-auto rounded-xl border border-border bg-surface">
              <table className="w-full min-w-[420px] text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                    <th className="px-4 py-3 font-medium">Consumer</th>
                    <th className="px-4 py-3 text-right font-medium">
                      Requests
                    </th>
                    <th className="px-4 py-3 text-right font-medium">
                      vs yesterday
                    </th>
                    <th className="px-4 py-3 text-right font-medium">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.clients ?? []).map((c) => (
                    <tr
                      key={c.client_id}
                      className="border-b border-border last:border-0"
                    >
                      <td className="px-4 py-3 text-text">
                        {names.get(c.client_id) ?? c.client_id}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-text-muted">
                        {c.request_count.toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {c.percent === null ? (
                          <span
                            className="text-[11px] text-text-muted"
                            title="No traffic yesterday"
                          >
                            new today
                          </span>
                        ) : (
                          <span
                            className={
                              c.percent > 0
                                ? "text-amber-600 dark:text-amber-400"
                                : "text-text-muted"
                            }
                          >
                            {c.percent > 0 ? "+" : ""}
                            {c.percent}%
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-text-muted">
                        {formatUsdCost(c.cost_usd)}
                      </td>
                    </tr>
                  ))}
                  {(data?.clients ?? []).length === 0 && (
                    <tr>
                      <td
                        colSpan={4}
                        className="p-8 text-center text-text-muted"
                      >
                        Nobody has been billed today yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {unhealthy.length > 0 && (
          <>
            <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-text-muted">
              Instances in error
            </h2>
            <div className="mt-3">
              <AttentionTable entries={unhealthy} />
            </div>
          </>
        )}

        <div className="mt-8 flex flex-wrap gap-2">
          <Link to="/activity" className={linkChipClass}>
            → Who is calling now
          </Link>
          <Link to="/limits" className={linkChipClass}>
            → Limits
          </Link>
          <Link to="/billing" className={linkChipClass}>
            → Billing
          </Link>
          <Link to="/instances" className={linkChipClass}>
            → Instances
          </Link>
        </div>
      </main>
    </div>
  );
}
