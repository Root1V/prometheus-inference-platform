import {
  AlertOctagon,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Bell,
  CircleCheck,
  RefreshCw,
  Search,
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
import { matchFeatures } from "../lib/features";
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

const METRICS: {
  key: Metric;
  label: string;
  colour: string;
  format: (n: number) => string;
}[] = [
  {
    key: "request_count",
    label: "Requests",
    colour: "var(--color-primary)",
    format: (n) => n.toLocaleString(),
  },
  {
    key: "tokens",
    label: "Tokens",
    colour: "#3b82f6",
    format: (n) => n.toLocaleString(),
  },
  {
    key: "cost_usd",
    label: "Cost",
    colour: "#10b981",
    format: (n) => formatUsdCost(n),
  },
];

/**
 * What an audit row was about, in words — PRM-247.
 *
 * `target` is the route's path parameters as JSON, so the feed read
 * `{"client_id": "d2e0e23d-8599-4cab-a730-aafb82f964d5"}` — forty characters
 * of punctuation and a UUID on a line meant to be scanned. The id is the
 * only part carrying meaning, and the principal list already turns it into a
 * name.
 *
 * Anything that does not parse is shown as it came: inventing a shape for it
 * would hide a row rather than explain it.
 */
function describeTarget(
  target: string | null,
  names: Map<string, string>,
): string | null {
  if (!target) return null;
  try {
    const parsed = JSON.parse(target) as Record<string, unknown>;
    return (
      Object.entries(parsed)
        .map(([key, value]) => {
          const text = String(value);
          return key.endsWith("client_id") ? (names.get(text) ?? text) : text;
        })
        .join(" · ") || null
    );
  } catch {
    return target;
  }
}

/**
 * The trend — PRM-247.
 *
 * Three series at once, because the question is whether they move together:
 * requests flat while tokens climb is a change in how the platform is being
 * used, and two charts seen one after the other do not answer it.
 *
 * **They are plotted against each series' own peak, and the axis says so.**
 * Requests, tokens and cost differ by four orders of magnitude here — 28
 * against 64,780 against 0.035 — so on a shared axis two of the three are a
 * flat line on the floor. Indexing each to its own maximum compares *shapes*,
 * which is the question; the tooltip carries the real values, because a
 * percentage of a peak is not a number anyone should read off an axis.
 *
 * Clicking a metric isolates it, and then the axis is that series' own units:
 * once there is one line there is nothing to normalise for, and showing a
 * real scale beats showing a percentage of one.
 */
function Trend({
  data,
  days,
  onDays,
}: {
  data: OverviewResponse;
  days: number;
  onDays: (d: number) => void;
}) {
  const [only, setOnly] = useState<Metric | null>(null);
  const shown = only ? METRICS.filter((m) => m.key === only) : METRICS;

  const peaks = Object.fromEntries(
    METRICS.map((m) => [
      m.key,
      Math.max(1, ...data.series.map((d) => d[m.key] || 0)),
    ]),
  ) as Record<Metric, number>;

  const plotted = data.series.map((row) => ({
    ...row,
    ...Object.fromEntries(
      METRICS.map((m) => [
        `${m.key}__pct`,
        ((row[m.key] || 0) / peaks[m.key]) * 100,
      ]),
    ),
  }));

  return (
    <div className="mt-3 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1">
          {data.windows.map((w) => (
            <button
              key={w}
              type="button"
              onClick={() => onDays(w)}
              className={
                "rounded-lg px-2.5 py-1 text-xs font-medium " +
                (days === w
                  ? "bg-primary text-primary-foreground"
                  : "border border-border text-text-muted hover:bg-background")
              }
            >
              {w} days
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {METRICS.map((m) => (
            <button
              key={m.key}
              type="button"
              onClick={() => setOnly(only === m.key ? null : m.key)}
              title={
                only === m.key
                  ? "Show all three again"
                  : `Show only ${m.label.toLowerCase()}, on its own scale`
              }
              className={
                "flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium " +
                (only === m.key
                  ? "border-transparent bg-primary text-primary-foreground"
                  : "border-border text-text-muted hover:bg-background")
              }
            >
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{
                  background: only === m.key ? "currentColor" : m.colour,
                }}
              />
              {m.label}
            </button>
          ))}
        </div>
      </div>

      <p className="mt-2 text-xs text-text-muted">
        {only
          ? `${METRICS.find((m) => m.key === only)!.label} over the last ${days} days, on its own scale.`
          : `Last ${days} days. Each line is scaled to its own peak so the three shapes can be compared — hover for the real values.`}{" "}
        From the usage rows, so a restart does not change this.
      </p>

      <div className="mt-3 h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={plotted}
            margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
          >
            <defs>
              {/* One gradient per series. Three filled areas overlap, so the
                  fill is faint and the stroke carries the line — at the
                  opacity a single area can afford, the one drawn last would
                  simply hide the other two. Isolating a metric gets the
                  stronger fill, because then there is nothing behind it. */}
              {METRICS.map((m) => (
                <linearGradient
                  key={m.key}
                  id={`fill-${m.key}`}
                  x1="0"
                  y1="0"
                  x2="0"
                  y2="1"
                >
                  <stop
                    offset="0%"
                    stopColor={m.colour}
                    stopOpacity={only ? 0.35 : 0.18}
                  />
                  <stop offset="100%" stopColor={m.colour} stopOpacity={0.02} />
                </linearGradient>
              ))}
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
              domain={only ? undefined : [0, 100]}
              tickFormatter={(v: number) =>
                only
                  ? METRICS.find((m) => m.key === only)!.format(v)
                  : `${Math.round(v)}%`
              }
            />
            <Tooltip
              contentStyle={{
                background: "var(--color-surface)",
                border: "1px solid var(--color-border)",
                borderRadius: 8,
                fontSize: 12,
              }}
              formatter={(value: unknown, name: unknown) => {
                const metric = METRICS.find((m) => m.label === name);
                if (!metric) return String(value);
                const row = Number(value ?? 0);
                // The real number, not the percentage that is plotted.
                return metric.format(
                  only ? row : (row / 100) * peaks[metric.key],
                );
              }}
            />
            {shown.map((m) => (
              <Area
                key={m.key}
                type="monotone"
                dataKey={only ? m.key : `${m.key}__pct`}
                name={m.label}
                stroke={m.colour}
                strokeWidth={2}
                dot={false}
                fill={`url(#fill-${m.key})`}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export default function Overview() {
  const [days, setDays] = useState(14);
  const [query, setQuery] = useState("");
  const [searchFocused, setSearchFocused] = useState(false);
  const overviewQuery = useOverview(days);
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

  const you = data?.you.client_id
    ? (names.get(data.you.client_id) ?? null)
    : null;
  const attentionCount = data?.attention.length ?? 0;

  // A search over what the page already holds, plus the catalogue of things
  // the platform can do. Clients and instances are the names an operator
  // already knows; the settings are the ones they cannot point at — tiers
  // live inside Limits, currency rates inside Billing — and those are what a
  // search is for. No endpoint and no index: it reaches nothing the operator
  // could not have reached anyway, it just saves the three clicks.
  const needle = query.trim().toLowerCase();
  const matches =
    needle === ""
      ? []
      : [
          ...(usersQuery.data ?? [])
            .filter((u) =>
              `${u.client_name} ${u.label ?? ""} ${u.client_id}`
                .toLowerCase()
                .includes(needle),
            )
            .map((u) => ({
              kind: "client",
              label: u.label || u.client_name || u.client_id,
              to: "/users",
            })),
          ...instances
            .filter((i) => i.id.toLowerCase().includes(needle))
            .map((i) => ({ kind: "instance", label: i.id, to: "/instances" })),
          ...matchFeatures(needle).map((f) => ({
            kind: f.where.toLowerCase(),
            label: f.label,
            to: f.to,
          })),
        ].slice(0, 10);

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
        {/* PRM-247: a header that says who is reading and lets them get
            somewhere, rather than a title and a button. The identity comes
            from the token — the server answers it, like Activity's `is_you` —
            and the name is resolved from the principal list this page already
            loads, because `Claims` carries no email and a field that is
            always null is worse than no field. */}
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold text-text">
              {you ? `Hello, ${you}` : "Overview"}
            </h1>
            <p className="mt-1 text-sm text-text-muted">
              {attentionCount > 0
                ? `${attentionCount} thing${attentionCount === 1 ? "" : "s"} need your attention.`
                : "Nothing needs your attention. Here is how the platform is running."}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search
                size={14}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted"
              />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Find a client, model or instance…"
                aria-label="Search"
                onFocus={() => setSearchFocused(true)}
                onBlur={() =>
                  window.setTimeout(() => setSearchFocused(false), 150)
                }
                // Grows leftwards on focus — the field sits at the right
                // edge, so widening it rightwards would push it off. 64 is
                // enough to read a client's name back and not enough to type
                // a question into.
                className={
                  "rounded-lg border border-border bg-surface py-1.5 pl-8 pr-2 text-sm text-text transition-[width] duration-150 placeholder:text-text-muted focus:border-primary focus:outline-none " +
                  (searchFocused ? "w-[26rem]" : "w-64")
                }
              />
              {query.trim() !== "" && (
                <div className="absolute left-0 right-0 top-full z-10 mt-1 max-h-72 overflow-y-auto rounded-lg border border-border bg-surface py-1 shadow-lg">
                  {matches.length === 0 ? (
                    <p className="px-3 py-2 text-xs text-text-muted">
                      Nothing matches.
                    </p>
                  ) : (
                    matches.map((m) => (
                      <Link
                        key={`${m.to}-${m.label}`}
                        to={m.to}
                        onClick={() => setQuery("")}
                        className="flex items-center justify-between gap-3 px-3 py-2 text-sm text-text hover:bg-background"
                      >
                        <span className="truncate">{m.label}</span>
                        <span className="shrink-0 text-[11px] uppercase tracking-wide text-text-muted">
                          {m.kind}
                        </span>
                      </Link>
                    ))
                  )}
                </div>
              )}
            </div>
            <a
              href="#needs-attention"
              title={
                attentionCount
                  ? `${attentionCount} open`
                  : "Nothing needs attention"
              }
              className="relative rounded-lg border border-border bg-surface p-2 text-text-muted hover:bg-background"
            >
              <Bell size={16} />
              {attentionCount > 0 && (
                <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-medium text-white">
                  {attentionCount}
                </span>
              )}
            </a>
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
        <p className="mt-2 text-xs tabular-nums text-text-muted">
          {activeNodes}/{nodes.length} nodes · {running} running · up{" "}
          {formatUptime(data?.fleet.uptime_seconds ?? 0)}
        </p>

        {overviewQuery.isError && (
          <p className="mt-4 rounded-xl border border-border bg-surface px-4 py-3 text-sm text-red-600">
            Could not read the platform figures.
          </p>
        )}

        {/* ── 1 · what needs attention ─────────────────────────────────── */}
        <h2
          id="needs-attention"
          className="mt-8 text-sm font-medium uppercase tracking-wide text-text-muted"
        >
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
        {data && <Trend data={data} days={days} onDays={setDays} />}

        {/* ── 4 · who, on what, and what changed ──────────────────────── */}
        {/*
          One grid of three, arranged by how tall each panel actually is.
          The first version paired them two and two and put an eight-row
          table beside a two-row one twice over, so half the lower page was
          blank: `Models` (8 rows) next to `Consumers` (2), and `Closest to a
          ceiling` (1) next to `What changed` (8).

          Now the two tall panels take the outer columns and the two short
          ones stack in the middle, which is the same content in roughly the
          same height. Three columns only from `xl`: at 1080px a table of
          four numeric columns inside a third of the width loses its last
          column off the right, and a 13-inch laptop is not an edge case.
        */}
        <div className="mt-8 grid grid-cols-1 gap-4 xl:grid-cols-3">
          <div>
            <h2 className="text-sm font-medium uppercase tracking-wide text-text-muted">
              Models, last {data?.days ?? days} days
            </h2>
            {/*
              Two bars per model, and the gap between them is the point:
              traffic share above, cost share below. Measured on this
              deployment, `gpt-oss-20b-mxfp4` takes 4,544 requests for USD
              5.99 while `qwen3vl-8b-q4` takes 3,385 for USD 10.31 — fewer
              calls, more money. In the table this was two numeric columns
              eight rows apart and nobody was going to divide them.
            */}
            <div className="mt-3 rounded-xl border border-border bg-surface p-4">
              {(data?.models ?? []).length === 0 ? (
                <p className="text-sm text-text-muted">
                  Nothing billed in this window.
                </p>
              ) : (
                <div className="space-y-3">
                  {(() => {
                    const models = data?.models ?? [];
                    const maxReq = Math.max(
                      1,
                      ...models.map((m) => m.request_count),
                    );
                    const maxCost = Math.max(
                      0.0000001,
                      ...models.map((m) => m.cost_usd ?? 0),
                    );
                    return models.map((m) => (
                      <div key={m.model_id}>
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="min-w-0 truncate font-mono text-xs text-text">
                            {m.model_id}
                          </span>
                          <span className="shrink-0 text-[11px] tabular-nums text-text-muted">
                            {m.latency
                              ? `p95 ${(m.latency.p95_ms / 1000).toFixed(1)}s`
                              : ""}
                          </span>
                        </div>
                        <div className="mt-1 flex items-center gap-2">
                          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-background">
                            <div
                              className="h-full rounded-full bg-primary"
                              style={{
                                width: `${(m.request_count / maxReq) * 100}%`,
                              }}
                            />
                          </div>
                          <span className="w-16 shrink-0 text-right text-[11px] tabular-nums text-text-muted">
                            {m.request_count.toLocaleString()}
                          </span>
                        </div>
                        <div className="mt-1 flex items-center gap-2">
                          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-background">
                            <div
                              className="h-full rounded-full bg-emerald-500"
                              style={{
                                width: `${((m.cost_usd ?? 0) / maxCost) * 100}%`,
                              }}
                            />
                          </div>
                          <span className="w-16 shrink-0 text-right text-[11px] tabular-nums text-text-muted">
                            {formatUsdCost(m.cost_usd)}
                          </span>
                        </div>
                      </div>
                    ));
                  })()}
                  <p className="pt-1 text-[11px] text-text-muted">
                    <span className="mr-1 inline-block h-1.5 w-3 rounded-full bg-primary align-middle" />
                    requests
                    <span className="ml-3 mr-1 inline-block h-1.5 w-3 rounded-full bg-emerald-500 align-middle" />
                    cost — each against the largest in the window
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* The middle column: two short panels stacked, so its height
              matches the two tall panels either side of it. */}
          <div className="space-y-4">
            <div>
              <h2 className="text-sm font-medium uppercase tracking-wide text-text-muted">
                Consumers today
              </h2>
              <div className="mt-3 rounded-xl border border-border bg-surface p-4">
                {(data?.clients ?? []).length === 0 ? (
                  <p className="text-sm text-text-muted">
                    Nobody has been billed today yet.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {(() => {
                      const clients = data?.clients ?? [];
                      const max = Math.max(
                        1,
                        ...clients.map((c) => c.request_count),
                      );
                      return clients.map((c) => (
                        <div key={c.client_id}>
                          <div className="flex items-baseline justify-between gap-2 text-sm">
                            <span className="min-w-0 truncate text-text">
                              {names.get(c.client_id) ?? c.client_id}
                            </span>
                            <span className="shrink-0 text-[11px] tabular-nums">
                              {c.percent === null ? (
                                <span className="text-text-muted">
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
                            </span>
                          </div>
                          <div className="mt-1 flex items-center gap-2">
                            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-background">
                              <div
                                className="h-full rounded-full bg-primary"
                                style={{
                                  width: `${(c.request_count / max) * 100}%`,
                                }}
                              />
                            </div>
                            <span className="w-24 shrink-0 text-right text-[11px] tabular-nums text-text-muted">
                              {c.request_count.toLocaleString()} ·{" "}
                              {formatUsdCost(c.cost_usd)}
                            </span>
                          </div>
                        </div>
                      ));
                    })()}
                  </div>
                )}
              </div>
            </div>
            <div>
              <h2 className="text-sm font-medium uppercase tracking-wide text-text-muted">
                Closest to a ceiling, right now
              </h2>
              {/* Attention above only fires past 80%. This is the shape below
                it, which is what you want before raising a tier: "nobody is
                near a limit" and "three consumers sit at 40%" are different
                facts and only one of them is quiet. */}
              <div className="mt-3 rounded-xl border border-border bg-surface p-4">
                {(data?.pressure ?? []).length === 0 ? (
                  <p className="text-sm text-text-muted">
                    No counter is standing this minute — nothing is being
                    measured against a ceiling right now.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {(data?.pressure ?? []).map((row) => (
                      <div key={row.identity}>
                        <div className="flex items-baseline justify-between gap-2 text-sm">
                          <span className="min-w-0 truncate text-text">
                            {names.get(row.identity) ?? row.identity}
                          </span>
                          <span
                            className={
                              "shrink-0 tabular-nums " +
                              (row.percent >= 100
                                ? "font-medium text-red-500"
                                : row.percent >= 80
                                  ? "text-amber-600 dark:text-amber-400"
                                  : "text-text-muted")
                            }
                          >
                            {row.percent}%
                          </span>
                        </div>
                        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-background">
                          <div
                            className={
                              "h-full rounded-full " +
                              (row.percent >= 100
                                ? "bg-red-500"
                                : row.percent >= 80
                                  ? "bg-amber-500"
                                  : "bg-primary")
                            }
                            style={{ width: `${Math.min(100, row.percent)}%` }}
                          />
                        </div>
                        <p className="mt-0.5 text-[11px] text-text-muted">
                          <span className="font-mono">{row.dimension}</span>
                          {row.endpoint === "*"
                            ? " · all endpoints"
                            : ` · ${row.endpoint}`}{" "}
                          · {row.used.toLocaleString()} of{" "}
                          {row.limit.toLocaleString()}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div>
            <h2 className="text-sm font-medium uppercase tracking-wide text-text-muted">
              What changed
            </h2>
            {/* The audit table (PRM-157) existed with no way in from here. On
                a platform where a limit, a price or a grant is edited from
                the UI, "what changed" belongs beside "what is happening":
                the second is often explained by the first. */}
            <div className="mt-3 overflow-hidden rounded-xl border border-border bg-surface">
              {(data?.changes ?? []).length === 0 ? (
                <p className="p-4 text-sm text-text-muted">
                  Nothing has been changed through the dashboard yet.
                </p>
              ) : (
                <ul className="divide-y divide-border">
                  {(data?.changes ?? []).map((change, i) => (
                    <li
                      key={`${change.at}-${i}`}
                      className="flex gap-3 px-4 py-2.5 text-sm"
                    >
                      <span
                        className={
                          "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full " +
                          // "ok" is what `audit.record` writes. This compared
                          // against "success", a value that never occurs, so every
                          // row rendered as a failure.
                          (change.outcome === "ok"
                            ? "bg-primary"
                            : "bg-red-500")
                        }
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-text">
                          <span className="font-mono text-xs">
                            {change.action}
                          </span>
                          {describeTarget(change.target, names) && (
                            <span className="text-text-muted">
                              {" "}
                              · {describeTarget(change.target, names)}
                            </span>
                          )}
                        </span>
                        <span className="text-[11px] text-text-muted">
                          {/* The audit row keeps an email where it had one
                              and a client id otherwise; the principal list
                              turns the second into a name the operator
                              recognises. */}
                          {change.actor
                            ? (names.get(change.actor) ?? change.actor)
                            : "unknown"}
                          {change.at &&
                            ` · ${new Date(change.at).toLocaleString()}`}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
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
