import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Download,
  Percent,
  Receipt,
  Users,
} from "lucide-react";
import { Fragment, useState, type CSSProperties } from "react";
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
  useBillingHistory,
  useBillingOverview,
  useBillingSummary,
} from "../api/billing";
import { downloadUsageExportCsv, useUsageExportRows } from "../api/usage";
import { useUsers } from "../api/users";
import { BudgetAlertBanner } from "../components/BudgetAlertBanner";
import { CurrencyRatesForm } from "../components/CurrencyRatesForm";
import { ModelPricingTable } from "../components/ModelPricingTable";
import { Sidebar } from "../components/Sidebar";
import { StatCard } from "../components/StatCard";
import { useToast } from "../context/ToastContext";
import { getErrorMessage } from "../lib/errors";
import { formatCurrency, formatUsdCost } from "../lib/format";
import type { BillingPeriodSummary } from "../types/billing";

/** Eight million tokens does not fit on a card, and `8,143,022` truncated to
 *  `8,143,0…` is worse than a rounded figure. The exact count is on the card's
 *  own tooltip, and in the period history below it. */
function compactTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return n.toLocaleString();
}

const CHART_COLOR = "var(--color-primary)";
const GRID_COLOR = "var(--color-border)";
const AXIS_COLOR = "var(--color-text-muted)";

function tooltipStyle(): CSSProperties {
  return {
    background: "var(--color-surface)",
    border: "1px solid var(--color-border)",
    borderRadius: 8,
    fontSize: 12,
    color: "var(--color-text)",
  };
}

/** Recharts' Tooltip formatter value can be number | string | array | undefined. */
function tooltipCostFormatter(value: unknown): string {
  return formatUsdCost(typeof value === "number" ? value : Number(value ?? 0));
}

// timeZone: "UTC" is required — periods are UTC calendar months (matching the
// backend's month_bounds()), and formatting a UTC midnight Date in a
// negative-offset local timezone (e.g. UTC-5) would otherwise display the
// previous day, which crosses into the previous month on the 1st.
const PERIOD_MONTH_FORMATTER = new Intl.DateTimeFormat(undefined, {
  month: "long",
  year: "numeric",
  timeZone: "UTC",
});

/** Last `count` UTC calendar months (most recent first) as "YYYY-MM" values,
 * paired with a human-readable label — used to populate the Period select
 * instead of a native <input type="month">, whose placeholder renders as an
 * unreadable "-------- de ----" in some locales.
 */
function recentPeriods(count: number): { value: string; label: string }[] {
  const now = new Date();
  return Array.from({ length: count }, (_, i) => {
    const d = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1),
    );
    const value = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
    return { value, label: PERIOD_MONTH_FORMATTER.format(d) };
  });
}

/**
 * A ranking with its share of the whole — PRM-221.
 *
 * Deliberately a table and not a chart: the two questions here are "who is
 * biggest" and "by how much", and a sorted list with a share bar answers both
 * without a reader having to map a bar back to an axis. It also carries the
 * tokens and requests behind each figure, which a chart has nowhere to put.
 */
function RankTable({
  title,
  rows,
  total,
}: {
  title: string;
  rows: { key: string; name: string; cost: number; sub: string }[];
  total: number;
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface">
      <p className="border-b border-border px-4 py-3 text-xs font-medium uppercase tracking-wide text-text-muted">
        {title}
      </p>
      {rows.length === 0 ? (
        <p className="p-8 text-center text-sm text-text-muted">
          No usage this period.
        </p>
      ) : (
        <div className="max-h-72 overflow-y-auto">
          {rows.map((row) => {
            const share = total > 0 ? (row.cost / total) * 100 : 0;
            return (
              <div
                key={row.key}
                className="flex items-center gap-3 border-b border-border px-4 py-2.5 last:border-0"
              >
                <div className="min-w-0 flex-1">
                  <p
                    className="truncate text-sm font-medium text-text"
                    title={row.name}
                  >
                    {row.name}
                  </p>
                  <p className="truncate text-xs text-text-muted">{row.sub}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <div className="h-1.5 w-16 overflow-hidden rounded-full bg-background">
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{
                        width: `${row.cost > 0 ? Math.max(2, share) : 0}%`,
                      }}
                    />
                  </div>
                  <span className="w-10 text-right text-xs tabular-nums text-text-muted">
                    {/* "<1%" rather than "0%": it cost something. */}
                    {total === 0 || row.cost === 0
                      ? "\u2014"
                      : share < 0.5
                        ? "<1%"
                        : `${share.toFixed(0)}%`}
                  </span>
                  {/* No fixed width, because the figure has no fixed length.
                      `formatUsdCost` keeps significant digits all the way
                      down, so a model costing USD 0.00000064 is fourteen
                      characters — `w-20` clipped it to "USD 0.00000" and
                      `w-28` still missed by three pixels. Rounding instead
                      would be the silent zero PRM-119 exists to prevent, so
                      the number sets the width and the name truncates. Right
                      edges still line up: it is the last cell in the row. */}
                  <span className="shrink-0 whitespace-nowrap text-right text-sm tabular-nums text-text">
                    {formatUsdCost(row.cost)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function CapIndicator({ summary }: { summary: BillingPeriodSummary }) {
  if (summary.monthly_spend_cap_usd === null) {
    return <span className="text-xs text-text-muted">No cap configured</span>;
  }
  // PRM-119: a cap with nothing priced against it cannot be drawn as a
  // fraction. Showing an empty bar would read as "0% of the cap used", which
  // is the same false reassurance the 0.00 total gave — and worse here,
  // because an unpriced model is never budget-checked at all (RM-60).
  if (summary.total_usd === null) {
    return (
      <span className="text-xs text-text-muted">
        Cap {formatUsdCost(summary.monthly_spend_cap_usd)} · spend unknown —
        nothing this period had a configured price
      </span>
    );
  }
  const percent = Math.min(
    100,
    (summary.total_usd / summary.monthly_spend_cap_usd) * 100,
  );
  const isOver = summary.total_usd >= summary.monthly_spend_cap_usd;
  return (
    <div className="mt-1">
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-background">
        <div
          className={isOver ? "h-full bg-red-500" : "h-full bg-primary"}
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="mt-1 block text-xs text-text-muted">
        {percent.toFixed(0)}% of {formatUsdCost(summary.monthly_spend_cap_usd)}{" "}
        cap
      </span>
    </div>
  );
}

/**
 * One row of the Period History table, with an expand toggle that shows the
 * same per-request detail as the CSV export (via the same /v1/usage/export
 * endpoint, parsed for display instead of downloaded) directly on the page —
 * only fetched once expanded, so periods nobody inspects cost nothing.
 */
function PeriodHistoryRow({
  periodSummary,
  clientId,
  isExporting,
  onExport,
}: {
  periodSummary: BillingPeriodSummary;
  clientId: string;
  isExporting: boolean;
  onExport: (p: BillingPeriodSummary) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const detailQuery = useUsageExportRows(
    {
      start: periodSummary.period_start,
      end: periodSummary.period_end,
      client_id: clientId,
    },
    expanded,
  );
  const rows = detailQuery.data ?? [];

  return (
    <Fragment>
      <tr
        className="cursor-pointer border-b border-border last:border-0 hover:bg-background/50"
        onClick={() => setExpanded((v) => !v)}
      >
        <td className="flex items-center gap-1.5 px-4 py-3 font-medium text-text">
          {expanded ? (
            <ChevronDown size={14} className="text-text-muted" />
          ) : (
            <ChevronRight size={14} className="text-text-muted" />
          )}
          {periodSummary.period}
        </td>
        <td className="px-4 py-3 text-text-muted">
          {formatUsdCost(periodSummary.subtotal_usd)}
          {periodSummary.unpriced_requests > 0 && (
            <span
              className="ml-1.5 text-xs text-yellow-800 dark:text-yellow-300"
              title={
                `${periodSummary.unpriced_requests} of ${periodSummary.request_count} requests ` +
                `used a model with no configured price, so they are not in this figure. ` +
                `Set a price on the Model pricing table below.`
              }
            >
              +{periodSummary.unpriced_requests} unpriced
            </span>
          )}
        </td>
        <td className="px-4 py-3 text-text-muted">
          {formatUsdCost(periodSummary.tax_amount_usd)}
        </td>
        <td className="px-4 py-3 text-text-muted">
          {formatUsdCost(periodSummary.total_usd)}
        </td>
        <td className="px-4 py-3 text-text-muted">
          {periodSummary.request_count.toLocaleString()}
        </td>
        <td className="px-4 py-3">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onExport(periodSummary);
            }}
            disabled={isExporting}
            className="flex items-center gap-1.5 text-xs font-medium text-primary hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Download size={14} />
            {isExporting ? "Exporting…" : "CSV"}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-border bg-background/30 last:border-0">
          <td colSpan={6} className="p-0">
            {detailQuery.isLoading ? (
              <div className="p-4 text-center text-xs text-text-muted">
                Loading detail…
              </div>
            ) : rows.length === 0 ? (
              <div className="p-4 text-center text-xs text-text-muted">
                No usage this period.
              </div>
            ) : (
              <div className="max-h-64 overflow-y-auto overflow-x-auto">
                <table className="w-full min-w-[560px] text-left text-xs">
                  <thead>
                    <tr className="sticky top-0 bg-surface text-text-muted">
                      <th className="px-4 py-2 font-medium">Recorded at</th>
                      <th className="px-4 py-2 font-medium">Model</th>
                      <th className="px-4 py-2 font-medium">Kind</th>
                      <th className="px-4 py-2 font-medium">Prompt tok.</th>
                      <th className="px-4 py-2 font-medium">Completion tok.</th>
                      <th className="px-4 py-2 font-medium">Images</th>
                      <th className="px-4 py-2 font-medium">Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, i) => (
                      <tr key={i} className="border-t border-border">
                        <td className="px-4 py-1.5 text-text-muted">
                          {new Date(row.recorded_at).toLocaleString()}
                        </td>
                        <td
                          className="px-4 py-1.5 text-text"
                          title={row.model_id}
                        >
                          {row.model_slug}
                        </td>
                        <td className="px-4 py-1.5 text-text-muted">
                          {row.request_kind}
                        </td>
                        <td className="px-4 py-1.5 text-text-muted">
                          {row.prompt_tokens.toLocaleString()}
                        </td>
                        <td className="px-4 py-1.5 text-text-muted">
                          {row.completion_tokens.toLocaleString()}
                        </td>
                        <td className="px-4 py-1.5 text-text-muted">
                          {row.image_count.toLocaleString()}
                        </td>
                        <td className="px-4 py-1.5 text-text-muted">
                          {formatUsdCost(row.cost_usd)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export default function Billing() {
  const usersQuery = useUsers();
  const users = usersQuery.data ?? [];
  const [clientId, setClientId] = useState("");
  const [period, setPeriod] = useState("");
  const { showToast } = useToast();
  const [exportingPeriod, setExportingPeriod] = useState<string | null>(null);

  const effectiveClientId = clientId || users[0]?.client_id || "";
  const summaryQuery = useBillingSummary(
    effectiveClientId,
    period || undefined,
  );
  const historyQuery = useBillingHistory(effectiveClientId, 6);
  const periodOptions = recentPeriods(12);
  /** The overview needs a concrete YYYY-MM; "" means the current month. */
  const effectivePeriod = period || periodOptions[0].value;
  const overviewQuery = useBillingOverview(effectivePeriod);
  const overview = overviewQuery.data;
  const nameOf = (clientId: string) =>
    users.find((u) => u.client_id === clientId)?.client_name ?? clientId;

  const summary = summaryQuery.data;
  const modelBreakdown = (summary?.by_model ?? [])
    .map((m) => ({ ...m, label: m.model_id, cost: m.cost_usd ?? 0 }))
    // Dearest first: a horizontal bar chart is read top-down, and the order
    // the group-by happened to produce is not an answer to "where did the
    // money go".
    .sort((a, b) => b.cost - a.cost);
  const dailyTrend = (summary?.daily ?? []).map((d) => ({
    ...d,
    cost: d.cost_usd ?? 0,
  }));

  async function handleExportPeriod(periodSummary: BillingPeriodSummary) {
    setExportingPeriod(periodSummary.period);
    try {
      await downloadUsageExportCsv({
        start: periodSummary.period_start,
        end: periodSummary.period_end,
        client_id: effectiveClientId,
      });
    } catch (error) {
      showToast(getErrorMessage(error), "error");
    } finally {
      setExportingPeriod(null);
    }
  }

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="min-w-0 flex-1 px-8 py-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-text">Billing</h1>
            <p className="mt-1 text-sm text-text-muted">
              What the platform billed this month, where it came from, and the
              detail per client — informative only, no card charging.
            </p>
          </div>
          <div className="flex items-end gap-3">
            {/* PRM-221: Period governs the whole page — the platform totals and
                the per-client detail alike — so it stays here. Client governs
                only the detail section, and moved down beside it. */}
            <label className="flex items-center gap-2 text-sm text-text-muted">
              Period
              <select
                value={period}
                onChange={(e) => setPeriod(e.target.value)}
                className="rounded-lg border border-border bg-surface px-3 py-1.5 text-text"
              >
                <option value="">Current ({periodOptions[0].label})</option>
                {periodOptions.slice(1).map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>

        <div className="mt-4">
          <BudgetAlertBanner />
        </div>

        {/* PRM-221: the platform first.
            Every billing endpoint was keyed by client, so this page opened on
            a client selector and could not answer its own first question —
            what did the platform bill this month. Answering it meant a request
            per client, in a loop. It also hid the one fact worth acting on:
            `qwen3vl-8b-q4` is 61% of this month's bill, and no single client's
            view ever showed that. */}
        {overview && (
          <>
            <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {/* Subtotal leads and tax is its own card, because
                  `tax_rate_percent` is per client: one blended total would add
                  figures computed at different rates and show the sum as a
                  single number. */}
              <StatCard
                label="Platform subtotal"
                value={formatUsdCost(overview.subtotal_usd)}
                sub={`${compactTokens(overview.total_tokens)} tokens · ${overview.request_count.toLocaleString()} requests`}
                toneReason={`${overview.total_tokens.toLocaleString()} tokens across ${overview.request_count.toLocaleString()} requests, before tax`}
                icon={Receipt}
              />
              <StatCard
                label="Tax across clients"
                value={formatUsdCost(overview.tax_amount_usd)}
                sub="rates are set per client"
                icon={Percent}
              />
              <StatCard
                label="Total billed"
                value={formatUsdCost(overview.total_usd)}
                sub={`${overview.client_count} client${overview.client_count === 1 ? "" : "s"} with usage`}
                icon={Users}
              />
              <StatCard
                label="Unpriced"
                value={overview.unpriced_requests.toLocaleString()}
                sub={
                  overview.unpriced_requests === 0
                    ? "every request this month was priced"
                    : "requests on a model with no price — not in the figures above"
                }
                tone={overview.unpriced_requests > 0 ? "warn" : "neutral"}
                toneReason={
                  overview.unpriced_requests > 0
                    ? "These requests happened and cost something; the subtotal does not include them. Set a price on the Model pricing table below."
                    : undefined
                }
                icon={AlertTriangle}
              />
            </div>

            <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
              <RankTable
                title="Most billed models"
                rows={overview.by_model.map((m) => ({
                  key: m.model_id,
                  name: m.model_id,
                  cost: m.cost_usd ?? 0,
                  sub: `${compactTokens(m.tokens)} tokens · ${m.request_count.toLocaleString()} requests`,
                }))}
                total={overview.subtotal_usd ?? 0}
              />
              <RankTable
                title="Biggest clients"
                rows={overview.by_client.map((c) => ({
                  key: c.client_id,
                  name: nameOf(c.client_id),
                  cost: c.subtotal_usd ?? 0,
                  sub: `${compactTokens(c.total_tokens)} tokens · ${c.request_count.toLocaleString()} requests`,
                }))}
                total={overview.subtotal_usd ?? 0}
              />
            </div>
          </>
        )}

        <div className="mt-10 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-6">
          <h2 className="text-sm font-medium uppercase tracking-wide text-text-muted">
            One client in detail
          </h2>
          {/* Beside the section it governs, not in the page header. */}
          <label className="flex items-center gap-2 text-sm text-text-muted">
            Client
            <select
              value={effectiveClientId}
              onChange={(e) => setClientId(e.target.value)}
              className="rounded-lg border border-border bg-surface px-3 py-1.5 text-text"
            >
              {users.map((u) => (
                <option key={u.client_id} value={u.client_id}>
                  {u.client_name}
                </option>
              ))}
            </select>
          </label>
        </div>

        {!effectiveClientId ? (
          <div className="mt-6 rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
            No clients yet — create one from Users first.
          </div>
        ) : summaryQuery.isLoading || !summary ? (
          <div className="mt-6 rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
            Loading billing summary…
          </div>
        ) : (
          <>
            <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {/* PRM-220: the period is already named in the selector above,
                  so repeating it here only made the label wrap to two lines in
                  its own card. What the subtotal was missing is what it bought:
                  8,143,022 tokens across 1,182 requests sat in this response
                  and were rendered nowhere, so the figure had no unit of work
                  behind it. */}
              <StatCard
                label="Subtotal"
                value={formatUsdCost(summary.subtotal_usd)}
                sub={`${compactTokens(summary.total_tokens)} tokens · ${summary.request_count.toLocaleString()} requests`}
                toneReason={`${summary.total_tokens.toLocaleString()} tokens across ${summary.request_count.toLocaleString()} requests`}
                icon={Receipt}
              />
              <StatCard
                label={`Tax (${summary.tax_rate_percent}%)`}
                value={formatUsdCost(summary.tax_amount_usd)}
                icon={Percent}
              />
              <div className="flex flex-col justify-center gap-1 rounded-xl border border-border bg-surface p-5 shadow-sm">
                <p className="text-sm text-text-muted">Total due</p>
                <p className="text-2xl font-semibold text-text">
                  {formatCurrency(
                    summary.total_usd,
                    summary.preferred_currency,
                    summary.exchange_rate_used,
                  )}
                </p>
                {summary.preferred_currency !== "USD" && (
                  <p className="text-xs text-text-muted">
                    {formatUsdCost(summary.total_usd)} at{" "}
                    {summary.exchange_rate_used} {summary.preferred_currency}
                    /USD
                  </p>
                )}
              </div>
              {/* A card that says "No cap configured" and nothing else is a
                  card explaining its own emptiness. Where there is no cap, it
                  says what that means instead. */}
              <div className="flex flex-col justify-center rounded-xl border border-border bg-surface p-5 shadow-sm">
                <p className="text-sm text-text-muted">Spend cap</p>
                <CapIndicator summary={summary} />
                {summary.monthly_spend_cap_usd === null && (
                  <p className="mt-1 text-xs text-text-muted">
                    Requests are never refused for cost on this client.
                  </p>
                )}
              </div>
            </div>

            <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
              {/* PRM-220, revised: the same ranking the platform section
                  uses, at client scope.

                  It was a bar chart, and reported as looking unfinished —
                  rightly: three 18px bars adrift in a 256px box, with the
                  gaps larger than the bars. Widening them would have treated
                  the symptom. This card asks the question the overview's
                  "Most billed models" already answers better, so it reuses
                  that component: one visual grammar for "ranked by cost",
                  denser, and carrying the tokens and requests behind each
                  figure, which the chart had nowhere to put. */}
              <RankTable
                title="Cost by model"
                rows={modelBreakdown.map((m) => ({
                  key: m.model_id,
                  name: m.model_id,
                  cost: m.cost,
                  sub: `${compactTokens(m.tokens)} tokens · ${m.request_count.toLocaleString()} requests`,
                }))}
                total={summary.subtotal_usd ?? 0}
              />

              <div className="rounded-xl border border-border bg-surface p-5">
                <h2 className="text-sm font-medium uppercase tracking-wide text-text-muted">
                  Daily spend this period
                </h2>
                <div className="mt-4 h-64">
                  {dailyTrend.length === 0 ? (
                    <p className="flex h-full items-center justify-center text-sm text-text-muted">
                      No usage this period.
                    </p>
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      {/* PRM-220, revised: an area again, but `linear`.
                          Bars made three days legible and a thirty-day month
                          unreadable, and the question asked of a period chart
                          is the trend — is this month climbing or falling.

                          The original objection stands and is narrower than
                          "no curve": `monotone` interpolates *curvature*
                          between points, inventing acceleration that was never
                          measured, and through few points it can draw a peak
                          higher than the dearest day. `linear` joins the
                          observations with straight segments and never leaves
                          the range of the data. The dots mark where a
                          measurement actually exists, so the line reads as
                          days joined up rather than a continuous function. */}
                      <AreaChart data={dailyTrend}>
                        <defs>
                          <linearGradient
                            id="dailySpendFill"
                            x1="0"
                            y1="0"
                            x2="0"
                            y2="1"
                          >
                            <stop
                              offset="0%"
                              stopColor={CHART_COLOR}
                              stopOpacity={0.35}
                            />
                            <stop
                              offset="100%"
                              stopColor={CHART_COLOR}
                              stopOpacity={0}
                            />
                          </linearGradient>
                        </defs>
                        <CartesianGrid
                          strokeDasharray="3 3"
                          stroke={GRID_COLOR}
                          vertical={false}
                        />
                        <XAxis
                          dataKey="day"
                          tick={{ fill: AXIS_COLOR, fontSize: 12 }}
                          axisLine={{ stroke: GRID_COLOR }}
                          tickLine={false}
                          minTickGap={24}
                        />
                        <YAxis
                          tick={{ fill: AXIS_COLOR, fontSize: 12 }}
                          axisLine={false}
                          tickLine={false}
                          tickFormatter={(v: number) => formatUsdCost(v)}
                          width={70}
                        />
                        <Tooltip
                          contentStyle={tooltipStyle()}
                          formatter={tooltipCostFormatter}
                        />
                        <Area
                          type="linear"
                          dataKey="cost"
                          stroke={CHART_COLOR}
                          fill="url(#dailySpendFill)"
                          strokeWidth={2}
                          dot={{ r: 2.5, fill: CHART_COLOR, strokeWidth: 0 }}
                          activeDot={{ r: 4 }}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  )}
                </div>
              </div>
            </div>

            <div className="mt-8">
              <h2 className="text-sm font-medium uppercase tracking-wide text-text-muted">
                Period history
              </h2>
              <div className="mt-3 overflow-x-auto rounded-xl border border-border bg-surface">
                {historyQuery.isLoading ? (
                  <div className="p-8 text-center text-text-muted">
                    Loading…
                  </div>
                ) : (
                  <table className="w-full min-w-[560px] text-left text-sm">
                    <thead>
                      <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                        <th className="px-4 py-3 font-medium">Period</th>
                        <th className="px-4 py-3 font-medium">Subtotal</th>
                        <th className="px-4 py-3 font-medium">Tax</th>
                        <th className="px-4 py-3 font-medium">Total</th>
                        <th className="px-4 py-3 font-medium">Requests</th>
                        <th className="px-4 py-3 font-medium">Export</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(historyQuery.data?.periods ?? []).map((p) => (
                        <PeriodHistoryRow
                          key={p.period}
                          periodSummary={p}
                          clientId={effectiveClientId}
                          isExporting={exportingPeriod === p.period}
                          onExport={handleExportPeriod}
                        />
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </>
        )}

        <div className="mt-8">
          <CurrencyRatesForm />
        </div>

        <div className="mt-8">
          <h2 className="text-sm font-medium uppercase tracking-wide text-text-muted">
            Model pricing
          </h2>
          <p className="mt-1 text-xs text-text-muted">
            Replaces hand-editing pricing.yaml — a saved price applies to the
            next request immediately, no restart needed.
          </p>
          <div className="mt-3 rounded-xl border border-border bg-surface">
            <div className="max-h-[35rem] overflow-y-auto overflow-x-auto">
              <ModelPricingTable />
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
