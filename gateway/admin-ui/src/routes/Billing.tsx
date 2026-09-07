import { Download, Percent, Receipt } from "lucide-react";
import { useState, type CSSProperties } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useBillingHistory, useBillingSummary } from "../api/billing";
import { downloadUsageExportCsv } from "../api/usage";
import { useUsers } from "../api/users";
import { BudgetAlertBanner } from "../components/BudgetAlertBanner";
import { ModelPricingTable } from "../components/ModelPricingTable";
import { Sidebar } from "../components/Sidebar";
import { StatCard } from "../components/StatCard";
import { useToast } from "../context/ToastContext";
import { getErrorMessage } from "../lib/errors";
import { formatCurrency, formatUsdCost } from "../lib/format";
import type { BillingPeriodSummary } from "../types/billing";

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
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1));
    const value = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
    return { value, label: PERIOD_MONTH_FORMATTER.format(d) };
  });
}

function CapIndicator({ summary }: { summary: BillingPeriodSummary }) {
  if (summary.monthly_spend_cap_usd === null) {
    return <span className="text-xs text-text-muted">No cap configured</span>;
  }
  const percent = Math.min(100, (summary.total_usd / summary.monthly_spend_cap_usd) * 100);
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
        {percent.toFixed(0)}% of {formatUsdCost(summary.monthly_spend_cap_usd)} cap
      </span>
    </div>
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
  const summaryQuery = useBillingSummary(effectiveClientId, period || undefined);
  const historyQuery = useBillingHistory(effectiveClientId, 6);
  const periodOptions = recentPeriods(12);

  const summary = summaryQuery.data;
  const modelBreakdown = (summary?.by_model ?? []).map((m) => ({
    ...m,
    label: m.model_id,
    cost: m.cost_usd ?? 0,
  }));
  const dailyTrend = (summary?.daily ?? []).map((d) => ({ ...d, cost: d.cost_usd ?? 0 }));

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
              Per-client cost, spend cap, and export — informative only, no card charging.
            </p>
          </div>
          <div className="flex items-end gap-3">
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
              <StatCard
                label={`Subtotal (${summary.period})`}
                value={formatUsdCost(summary.subtotal_usd)}
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
                    {formatUsdCost(summary.total_usd)} at {summary.exchange_rate_used}{" "}
                    {summary.preferred_currency}/USD
                  </p>
                )}
              </div>
              <div className="flex flex-col justify-center rounded-xl border border-border bg-surface p-5 shadow-sm">
                <p className="text-sm text-text-muted">Spend cap</p>
                <CapIndicator summary={summary} />
              </div>
            </div>

            <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div className="rounded-xl border border-border bg-surface p-5">
                <h2 className="text-sm font-medium uppercase tracking-wide text-text-muted">
                  Cost by model
                </h2>
                <div className="mt-4 h-64">
                  {modelBreakdown.length === 0 ? (
                    <p className="flex h-full items-center justify-center text-sm text-text-muted">
                      No usage this period.
                    </p>
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={modelBreakdown}>
                        <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} vertical={false} />
                        <XAxis
                          dataKey="label"
                          tick={{ fill: AXIS_COLOR, fontSize: 12 }}
                          axisLine={{ stroke: GRID_COLOR }}
                          tickLine={false}
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
                        <Bar dataKey="cost" fill={CHART_COLOR} radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  )}
                </div>
              </div>

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
                      <AreaChart data={dailyTrend}>
                        <defs>
                          <linearGradient id="dailySpendFill" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor={CHART_COLOR} stopOpacity={0.35} />
                            <stop offset="100%" stopColor={CHART_COLOR} stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} vertical={false} />
                        <XAxis
                          dataKey="day"
                          tick={{ fill: AXIS_COLOR, fontSize: 12 }}
                          axisLine={{ stroke: GRID_COLOR }}
                          tickLine={false}
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
                          type="monotone"
                          dataKey="cost"
                          stroke={CHART_COLOR}
                          fill="url(#dailySpendFill)"
                          strokeWidth={2}
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
                  <div className="p-8 text-center text-text-muted">Loading…</div>
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
                        <tr key={p.period} className="border-b border-border last:border-0">
                          <td className="px-4 py-3 font-medium text-text">{p.period}</td>
                          <td className="px-4 py-3 text-text-muted">
                            {formatUsdCost(p.subtotal_usd)}
                          </td>
                          <td className="px-4 py-3 text-text-muted">
                            {formatUsdCost(p.tax_amount_usd)}
                          </td>
                          <td className="px-4 py-3 text-text-muted">{formatUsdCost(p.total_usd)}</td>
                          <td className="px-4 py-3 text-text-muted">
                            {p.request_count.toLocaleString()}
                          </td>
                          <td className="px-4 py-3">
                            <button
                              type="button"
                              onClick={() => handleExportPeriod(p)}
                              disabled={exportingPeriod === p.period}
                              className="flex items-center gap-1.5 text-xs font-medium text-primary hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <Download size={14} />
                              {exportingPeriod === p.period ? "Exporting…" : "CSV"}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </>
        )}

        <div className="mt-8">
          <h2 className="text-sm font-medium uppercase tracking-wide text-text-muted">
            Model pricing
          </h2>
          <p className="mt-1 text-xs text-text-muted">
            Replaces hand-editing pricing.yaml — a saved price applies to the next request
            immediately, no restart needed.
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
