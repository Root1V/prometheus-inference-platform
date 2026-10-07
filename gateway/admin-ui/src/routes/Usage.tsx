import { ChevronDown, ChevronRight, Download } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  downloadUsageExportCsv,
  useUsageRange,
  type UsageRangeClient,
} from "../api/usage";
import { useUsers } from "../api/users";
import { Sidebar } from "../components/Sidebar";
import { useToast } from "../context/ToastContext";
import { getErrorMessage } from "../lib/errors";
import { formatUsdCost } from "../lib/format";

/** Today (UTC) as YYYY-MM-DD, for the export range's default bounds. */
function todayUtc(): string {
  return new Date().toISOString().slice(0, 10);
}

/** First day of the current UTC month, as YYYY-MM-DD. */
function monthStartUtc(): string {
  return `${todayUtc().slice(0, 7)}-01`;
}

/**
 * What a request kind costs you is not what it bills you in tokens — PRM-208.
 *
 * `image` is the reason this exists at all: it produces **zero tokens** and
 * still costs money. Six image requests on this platform are 9% of the bill
 * and 0% of every token figure on the page.
 */
const KIND_STYLE: Record<string, string> = {
  chat: "bg-primary/10 text-primary",
  embedding: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
  rerank: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
  predict: "bg-teal-500/10 text-teal-600 dark:text-teal-400",
  image: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
};

function KindBadge({ kind }: { kind: string }) {
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
        KIND_STYLE[kind] ?? "bg-background text-text-muted"
      }`}
    >
      {kind}
    </span>
  );
}

/**
 * USD per million tokens — the one number that says "this client is on an
 * expensive model", and the one the page made you compute by dividing two
 * columns by hand. Measured across live clients it spans 800x, from $0.10 to
 * $80.38 per million.
 *
 * Returns null rather than Infinity when there are no tokens: an image-only
 * client has a real cost and no token denominator, and a rate printed there
 * would be arithmetic rather than information.
 */
function ratePerMillionTokens(costUsd: number | null, tokens: number): number | null {
  if (costUsd === null || tokens <= 0) return null;
  return (costUsd / tokens) * 1_000_000;
}

export default function Usage() {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  /**
   * PRM-208: one range drives everything — the chart, the client ranking and
   * the CSV. Before this the page had two date mechanisms that looked like one:
   * a `Day` picker for the table and a From/To pair that silently only fed the
   * export. Collapsing them removes the ambiguity rather than labelling it.
   */
  const [rangeStart, setRangeStart] = useState(monthStartUtc);
  const [rangeEnd, setRangeEnd] = useState(todayUtc);
  const exportStart = rangeStart;
  const exportEnd = rangeEnd;
  const [exporting, setExporting] = useState(false);
  const rangeQuery = useUsageRange(rangeStart, rangeEnd);
  const usersQuery = useUsers();
  const { showToast } = useToast();

  const users = usersQuery.data ?? [];
  const nameByClientId = new Map(users.map((u) => [u.client_id, u.client_name]));
  /**
   * The label is the human sentence about what a credential is *for* — "Argus
   * monitoring collector" rather than `d14c79a6`. Without it the ranking names
   * its rows well enough to look them up on another page and not well enough
   * to act on here, which is the same as not naming them.
   */
  const labelByClientId = new Map(users.map((u) => [u.client_id, u.label]));

  const daily = useMemo(() => rangeQuery.data?.daily ?? [], [rangeQuery.data]);
  const rangeClients: UsageRangeClient[] = useMemo(
    () =>
      [...(rangeQuery.data?.by_client ?? [])].sort(
        (a, b) => (b.cost_usd ?? 0) - (a.cost_usd ?? 0) || b.total_tokens - a.total_tokens,
      ),
    [rangeQuery.data],
  );
  const rangeTotals = useMemo(
    () =>
      daily.reduce(
        (acc, d) => ({
          requests: acc.requests + d.request_count,
          tokens: acc.tokens + d.tokens,
          cost: acc.cost + (d.cost_usd ?? 0),
          unpriced: acc.unpriced + d.unpriced_requests,
        }),
        { requests: 0, tokens: 0, cost: 0, unpriced: 0 },
      ),
    [daily],
  );
  /**
   * Share is measured in **cost**, not tokens — PRM-208.
   *
   * It used to be tokens, and that made the bar contradict the column beside
   * it: the client with six image requests is the fourth most expensive on the
   * platform and produced no tokens at all, so it rendered as a bar at zero
   * next to a cost of $1.54. The table is sorted by cost; the bar now measures
   * the same thing the sort does.
   */
  const rangeCostTotal = rangeClients.reduce((n, c) => n + (c.cost_usd ?? 0), 0);
  /** Stated in the UI so the cap column cannot be mistaken for range data. */
  const monthToDateStart = rangeQuery.data?.month_to_date_start ?? monthStartUtc();

  /** Presets, because "the last 7 days" is the question people actually have
   * and typing two ISO dates to ask it is friction with no payoff. */
  const applyPreset = (days: number) => {
    const end = new Date();
    const start = new Date(end.getTime() - (days - 1) * 86_400_000);
    setRangeStart(start.toISOString().slice(0, 10));
    setRangeEnd(end.toISOString().slice(0, 10));
  };

  async function handleExport() {
    setExporting(true);
    try {
      await downloadUsageExportCsv({ start: exportStart, end: exportEnd });
    } catch (error) {
      showToast(getErrorMessage(error), "error");
    } finally {
      setExporting(false);
    }
  }

  function toggleExpanded(clientId: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(clientId)) {
        next.delete(clientId);
      } else {
        next.add(clientId);
      }
      return next;
    });
  }

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="min-w-0 flex-1 px-8 py-8">
        <div>
          <h1 className="text-2xl font-semibold text-text">Usage</h1>
          <p className="mt-1 text-sm text-text-muted">
            Who consumed what, across a range you choose. Dates are UTC and
            inclusive at both ends.
          </p>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface p-3">
          <div className="flex items-center gap-1 rounded-lg bg-background p-1">
            {[
              { label: "7 days", days: 7 },
              { label: "30 days", days: 30 },
              { label: "90 days", days: 90 },
            ].map((preset) => (
              <button
                key={preset.days}
                type="button"
                onClick={() => applyPreset(preset.days)}
                className="rounded-md px-3 py-1.5 text-sm font-medium text-text-muted transition-colors hover:bg-surface hover:text-text"
              >
                {preset.label}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-2 text-sm text-text-muted">
            From
            <input
              type="date"
              value={rangeStart}
              max={rangeEnd}
              onChange={(e) => setRangeStart(e.target.value)}
              className="rounded-lg border border-border bg-background px-3 py-1.5 text-text"
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-text-muted">
            To
            <input
              type="date"
              value={rangeEnd}
              min={rangeStart}
              onChange={(e) => setRangeEnd(e.target.value)}
              className="rounded-lg border border-border bg-background px-3 py-1.5 text-text"
            />
          </label>
          <button
            type="button"
            onClick={handleExport}
            disabled={exporting}
            title="Every request in the range, with the exact rate applied at the time, plus a reconciliation total row"
            className="ml-auto flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm font-medium text-text transition-colors hover:bg-background disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Download size={15} />
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
        </div>

        {/* PRM-208: the range totals. Four cards rather than three because
            `unpriced` earns its place — RM-33 never bills an unpriced model as
            $0, so a cost figure that omitted some requests has to say how many,
            or it is a confident number that is quietly incomplete. */}
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-xl border border-border bg-surface p-4">
            <p className="text-sm text-text-muted">Requests</p>
            <p className="text-2xl font-semibold text-text">
              {rangeTotals.requests.toLocaleString()}
            </p>
            <p className="text-xs text-text-muted">
              over {daily.length} day{daily.length === 1 ? "" : "s"} with traffic
            </p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4">
            <p className="text-sm text-text-muted">Tokens</p>
            <p className="text-2xl font-semibold text-text">
              {rangeTotals.tokens.toLocaleString()}
            </p>
            <p className="text-xs text-text-muted">prompt and completion combined</p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4">
            <p className="text-sm text-text-muted">Estimated cost</p>
            <p className="text-2xl font-semibold text-text">{formatUsdCost(rangeTotals.cost)}</p>
            <p className="text-xs text-text-muted">
              {rangeTotals.unpriced > 0
                ? `excludes ${rangeTotals.unpriced.toLocaleString()} unpriced request${rangeTotals.unpriced === 1 ? "" : "s"}`
                : "every request in range was priced"}
            </p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4">
            <p className="text-sm text-text-muted">Clients</p>
            <p className="text-2xl font-semibold text-text">{rangeClients.length}</p>
            <p className="text-xs text-text-muted">
              {rangeClients[0]
                ? `${nameByClientId.get(rangeClients[0].client_id) ?? "top client"} leads`
                : "no usage in range"}
            </p>
          </div>
        </div>

        {/* The trend. Billing has one of these per client; "every client over
            time" was answerable nowhere, which is what left this page showing
            two rows and half a screen of nothing. */}
        <div className="mt-4 rounded-xl border border-border bg-surface p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
            Tokens per day
          </p>
          <div className="mt-3 h-56">
            {rangeQuery.isLoading ? (
              <div className="flex h-full items-center justify-center text-sm text-text-muted">
                Loading…
              </div>
            ) : daily.length === 0 ? (
              <div className="flex h-full items-center justify-center text-sm text-text-muted">
                No usage in this range.
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={daily}>
                  <defs>
                    <linearGradient id="usageTokensFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--color-primary)" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="var(--color-primary)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="var(--color-border)"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="day"
                    tick={{ fill: "var(--color-text-muted)", fontSize: 12 }}
                    axisLine={{ stroke: "var(--color-border)" }}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fill: "var(--color-text-muted)", fontSize: 12 }}
                    axisLine={false}
                    tickLine={false}
                    width={60}
                    tickFormatter={(v: number) =>
                      v >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M` : `${Math.round(v / 1000)}k`
                    }
                  />
                  <Tooltip
                    contentStyle={{
                      background: "var(--color-surface)",
                      border: "1px solid var(--color-border)",
                      borderRadius: "0.5rem",
                      color: "var(--color-text)",
                    }}
                    formatter={(v: unknown) => [Number(v).toLocaleString(), "tokens"]}
                  />
                  <Area
                    type="monotone"
                    dataKey="tokens"
                    stroke="var(--color-primary)"
                    fill="url(#usageTokensFill)"
                    strokeWidth={2}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/*
          PRM-208: the one table on this page, and it covers the range.

          It used to have a twin — a second client table under its own `Day`
          picker, carrying the per-model split. Two tables of the same clients
          under two date controls is a page asking the reader to work out which
          number answers their question. The split was the part worth keeping,
          so it moved here as an expandable row and the day picker went.
        */}
        <div className="mt-4 overflow-hidden rounded-xl border border-border bg-surface">
          <p className="border-b border-border px-4 py-3 text-xs font-medium uppercase tracking-wide text-text-muted">
            Clients in this range
          </p>
          {rangeQuery.isLoading ? (
            <div className="p-12 text-center text-text-muted">Loading usage…</div>
          ) : rangeQuery.isError ? (
            <div className="p-12 text-center text-red-600">
              {getErrorMessage(rangeQuery.error)}
            </div>
          ) : rangeClients.length === 0 ? (
            <div className="p-12 text-center text-text-muted">
              No usage recorded between {rangeStart} and {rangeEnd}.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1000px] text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                    <th className="px-4 py-2 font-medium">Client</th>
                    <th className="px-4 py-2 font-medium">Requests</th>
                    <th className="px-4 py-2 font-medium">Tokens</th>
                    <th className="px-4 py-2 font-medium">Est. cost</th>
                    <th
                      className="px-4 py-2 font-medium"
                      title="Effective price this client is paying per million tokens — high means expensive models, not heavy use"
                    >
                      USD / Mtok
                    </th>
                    <th
                      className="px-4 py-2 font-medium"
                      title={`Spend since ${monthToDateStart} against the configured monthly cap. This column is the calendar month, not the range selected above.`}
                    >
                      Month vs cap
                    </th>
                    <th className="px-4 py-2 font-medium">Share of cost</th>
                  </tr>
                </thead>
                <tbody>
                  {rangeClients.map((c) => {
                    const isExpanded = expanded.has(c.client_id);
                    const hasBreakdown = c.by_model.length > 0;
                    const label = labelByClientId.get(c.client_id);
                    const rate = ratePerMillionTokens(c.cost_usd, c.total_tokens);
                    const avgTokens =
                      c.request_count > 0 ? Math.round(c.total_tokens / c.request_count) : 0;
                    const promptShare =
                      c.total_tokens > 0 ? (c.prompt_tokens / c.total_tokens) * 100 : null;
                    const costShare =
                      rangeCostTotal > 0 ? ((c.cost_usd ?? 0) / rangeCostTotal) * 100 : 0;
                    const cap = c.monthly_spend_cap_usd;
                    const mtd = c.month_to_date_cost_usd ?? 0;
                    const capUsed = cap && cap > 0 ? (mtd / cap) * 100 : null;
                    return (
                      <Fragment key={c.client_id}>
                        <tr
                          className={`border-b border-border last:border-0 ${
                            hasBreakdown ? "cursor-pointer hover:bg-background/50" : ""
                          }`}
                          onClick={hasBreakdown ? () => toggleExpanded(c.client_id) : undefined}
                        >
                          <td className="px-4 py-2">
                            <div className="flex items-start gap-1.5">
                              {hasBreakdown ? (
                                isExpanded ? (
                                  <ChevronDown size={14} className="mt-1 shrink-0 text-text-muted" />
                                ) : (
                                  <ChevronRight
                                    size={14}
                                    className="mt-1 shrink-0 text-text-muted"
                                  />
                                )
                              ) : (
                                <span className="w-[14px] shrink-0" />
                              )}
                              <div className="min-w-0">
                                <p className="font-medium text-text">
                                  {nameByClientId.get(c.client_id) ?? c.client_id}
                                </p>
                                {label && (
                                  <p className="truncate text-xs text-text-muted" title={label}>
                                    {label}
                                  </p>
                                )}
                              </div>
                            </div>
                          </td>
                          <td className="px-4 py-2 tabular-nums text-text-muted">
                            {c.request_count.toLocaleString()}
                          </td>
                          {/* Tokens, and the two things that decide which lever
                              to pull on them: the average size of a request and
                              how much of it is prompt. A client at 7,121 tokens
                              a request needs its context trimmed; one at 37
                              tokens across 776 calls needs a rate limit. Same
                              bill, opposite remedy. And completion tokens price
                              at roughly 4-5x prompt, so the split decides
                              between caching the prompt and capping
                              max_tokens. */}
                          <td className="px-4 py-2 tabular-nums text-text-muted">
                            {c.total_tokens.toLocaleString()}
                            <p className="text-xs text-text-muted/70">
                              {promptShare === null
                                ? "no tokens"
                                : `${promptShare.toFixed(0)}% prompt · ${avgTokens.toLocaleString()}/req`}
                            </p>
                          </td>
                          <td className="px-4 py-2 tabular-nums text-text-muted">
                            {formatUsdCost(c.cost_usd)}
                            {c.unpriced_requests > 0 && (
                              <p
                                className="text-xs text-amber-600 dark:text-amber-400"
                                title={`${c.unpriced_requests} request(s) had no configured price and are not in this figure`}
                              >
                                +{c.unpriced_requests} unpriced
                              </p>
                            )}
                          </td>
                          <td className="px-4 py-2 tabular-nums text-text-muted">
                            {rate === null ? (
                              <span title="No tokens to divide by — this client's cost is per-image">
                                —
                              </span>
                            ) : (
                              `$${rate.toFixed(2)}`
                            )}
                          </td>
                          <td className="px-4 py-2 tabular-nums">
                            {cap === null ? (
                              <span className="text-text-muted/60" title="No monthly cap configured">
                                —
                              </span>
                            ) : (
                              <>
                                <p
                                  className={
                                    capUsed !== null && capUsed >= 100
                                      ? "font-medium text-red-600 dark:text-red-400"
                                      : capUsed !== null && capUsed >= 80
                                        ? "font-medium text-amber-600 dark:text-amber-400"
                                        : "text-text-muted"
                                  }
                                >
                                  {formatUsdCost(mtd)} / {formatUsdCost(cap)}
                                </p>
                                <p className="text-xs text-text-muted/70">
                                  {capUsed === null ? "—" : `${capUsed.toFixed(0)}% of cap`}
                                </p>
                              </>
                            )}
                          </td>
                          <td className="px-4 py-2">
                            <div className="flex items-center gap-2">
                              <div className="h-1.5 w-20 overflow-hidden rounded-full bg-background">
                                <div
                                  className="h-full rounded-full bg-primary"
                                  style={{
                                    width: `${rangeCostTotal > 0 && (c.cost_usd ?? 0) > 0 ? Math.max(2, costShare) : 0}%`,
                                  }}
                                />
                              </div>
                              <span className="text-xs tabular-nums text-text-muted">
                                {/* "<1%" rather than "0%": a client that spent
                                    anything did use the platform, and a bar
                                    drawn beside a zero contradicts itself. */}
                                {rangeCostTotal === 0 || (c.cost_usd ?? 0) === 0
                                  ? "—"
                                  : costShare < 0.5
                                    ? "<1%"
                                    : `${costShare.toFixed(0)}%`}
                              </span>
                            </div>
                          </td>
                        </tr>
                        {isExpanded &&
                          c.by_model.map((m) => (
                            <tr
                              key={`${c.client_id}-${m.model_id}-${m.request_kind}`}
                              className="border-b border-border bg-background/30 last:border-0"
                            >
                              <td className="px-4 py-2 pl-10">
                                <div className="flex items-center gap-2">
                                  <span className="text-text-muted">{m.model_id}</span>
                                  <KindBadge kind={m.request_kind} />
                                </div>
                              </td>
                              <td className="px-4 py-2 tabular-nums text-text-muted">
                                {m.request_count.toLocaleString()}
                              </td>
                              <td
                                className="px-4 py-2 tabular-nums text-text-muted"
                                title={`${m.prompt_tokens.toLocaleString()} prompt · ${m.completion_tokens.toLocaleString()} completion`}
                              >
                                {/* An image row genuinely has no tokens. Saying
                                    so beats a bare 0 that reads like a bug. */}
                                {m.total_tokens > 0
                                  ? m.total_tokens.toLocaleString()
                                  : m.image_count > 0
                                    ? `${m.image_count.toLocaleString()} image${m.image_count === 1 ? "" : "s"}`
                                    : "—"}
                              </td>
                              <td className="px-4 py-2 tabular-nums text-text-muted">
                                {formatUsdCost(m.cost_usd)}
                              </td>
                              <td className="px-4 py-2 tabular-nums text-text-muted">
                                {(() => {
                                  const r = ratePerMillionTokens(m.cost_usd, m.total_tokens);
                                  return r === null ? "—" : `$${r.toFixed(2)}`;
                                })()}
                              </td>
                              <td className="px-4 py-2" />
                              <td className="px-4 py-2" />
                            </tr>
                          ))}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
