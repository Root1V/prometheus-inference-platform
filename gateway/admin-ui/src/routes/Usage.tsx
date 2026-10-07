import { ChevronDown, ChevronRight, Download } from "lucide-react";
import { Fragment, useState } from "react";
import { downloadUsageExportCsv, useUsage } from "../api/usage";
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

export default function Usage() {
  const [date, setDate] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [exportStart, setExportStart] = useState(monthStartUtc);
  const [exportEnd, setExportEnd] = useState(todayUtc);
  const [exporting, setExporting] = useState(false);
  const usageQuery = useUsage(date || undefined);
  const usersQuery = useUsers();
  const { showToast } = useToast();

  const users = usersQuery.data ?? [];
  const nameByClientId = new Map(users.map((u) => [u.client_id, u.client_name]));
  const entries = usageQuery.data?.data ?? [];

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

  /**
   * PRM-207: sorted by what was spent, biggest first.
   *
   * The API returns whatever order the group-by produced, and the question
   * asked of this page is "who is using the platform" — which is answered by
   * reading down a sorted column, not by scanning an arbitrary one. Falls back
   * to tokens so an unpriced client still sorts sensibly rather than sinking to
   * the bottom as a null.
   */
  const ranked = [...entries].sort(
    (a, b) =>
      (b.estimated_cost_usd ?? 0) - (a.estimated_cost_usd ?? 0) || b.total_tokens - a.total_tokens,
  );

  const dayTotals = ranked.reduce(
    (acc, e) => ({
      requests: acc.requests + e.request_count,
      tokens: acc.tokens + e.total_tokens,
      cost: acc.cost + (e.estimated_cost_usd ?? 0),
      anyPriced: acc.anyPriced || e.estimated_cost_usd !== null,
    }),
    { requests: 0, tokens: 0, cost: 0, anyPriced: false },
  );

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="min-w-0 flex-1 px-8 py-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-text">Usage</h1>
            <p className="mt-1 text-sm text-text-muted">
              Token usage per client, with a per-model breakdown, for{" "}
              {usageQuery.data?.window ?? "today"} (UTC).
            </p>
          </div>
          {/*
            PRM-207: labelled "Showing", because this page had two date controls
            that looked like one mechanism. This one drives the table; the
            From/To pair below drives only the CSV. Nothing said so, and a pair
            of date inputs sitting under a table reads as a filter on it.
          */}
          <label className="flex items-center gap-2 text-sm text-text-muted">
            Showing
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="rounded-lg border border-border bg-surface px-3 py-1.5 text-text"
            />
          </label>
        </div>

        <div className="mt-4 rounded-xl border border-border bg-surface p-4">
          <p className="mb-3 text-xs font-medium uppercase tracking-wide text-text-muted">
            Export a date range — does not change the table below
          </p>
          <div className="flex flex-wrap items-end gap-3">
          <label className="flex items-center gap-2 text-sm text-text-muted">
            From
            <input
              type="date"
              value={exportStart}
              max={exportEnd}
              onChange={(e) => setExportStart(e.target.value)}
              className="rounded-lg border border-border bg-background px-3 py-1.5 text-text"
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-text-muted">
            To
            <input
              type="date"
              value={exportEnd}
              min={exportStart}
              onChange={(e) => setExportEnd(e.target.value)}
              className="rounded-lg border border-border bg-background px-3 py-1.5 text-text"
            />
          </label>
          <button
            type="button"
            onClick={handleExport}
            disabled={exporting}
            className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Download size={16} />
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
          <p className="text-xs text-text-muted">
            Every request in the range, with the exact rate applied at the time — a reconciliation
            total row is appended.
          </p>
          </div>
        </div>

        {/*
          PRM-207: the day's totals, because reading them off the table meant
          adding up rows by hand. The cost carries a caveat rather than a bare
          number — RM-33 never bills an unpriced model as $0, so a total that
          silently omitted one would be the same lie in aggregate.
        */}
        {ranked.length > 0 && (
          <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="rounded-xl border border-border bg-surface p-4">
              <p className="text-sm text-text-muted">Requests</p>
              <p className="text-2xl font-semibold text-text">
                {dayTotals.requests.toLocaleString()}
              </p>
              <p className="text-xs text-text-muted">
                across {ranked.length} client{ranked.length === 1 ? "" : "s"}
              </p>
            </div>
            <div className="rounded-xl border border-border bg-surface p-4">
              <p className="text-sm text-text-muted">Tokens</p>
              <p className="text-2xl font-semibold text-text">{dayTotals.tokens.toLocaleString()}</p>
              <p className="text-xs text-text-muted">prompt and completion combined</p>
            </div>
            <div className="rounded-xl border border-border bg-surface p-4">
              <p className="text-sm text-text-muted">Estimated cost</p>
              <p className="text-2xl font-semibold text-text">
                {dayTotals.anyPriced ? formatUsdCost(dayTotals.cost) : "—"}
              </p>
              <p className="text-xs text-text-muted">
                {dayTotals.anyPriced
                  ? "priced models only — unpriced ones are excluded, never counted as $0"
                  : "no model in this day has a configured price"}
              </p>
            </div>
          </div>
        )}

        <div className="mt-6 overflow-x-auto rounded-xl border border-border bg-surface">
          {usageQuery.isLoading ? (
            <div className="p-12 text-center text-text-muted">Loading usage…</div>
          ) : usageQuery.isError ? (
            <div className="p-12 text-center text-red-600">{getErrorMessage(usageQuery.error)}</div>
          ) : entries.length === 0 ? (
            <div className="p-12 text-center text-text-muted">
              No usage recorded for {date || "today"} yet.
            </div>
          ) : (
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                  <th className="px-4 py-3 font-medium">Client</th>
                  <th className="px-4 py-3 font-medium">Prompt tokens</th>
                  <th className="px-4 py-3 font-medium">Completion tokens</th>
                  <th className="px-4 py-3 font-medium">Total tokens</th>
                  <th className="px-4 py-3 font-medium">Requests</th>
                  {/* PRM-207: four cost columns became one. The split by prompt,
                      completion and image is still there, on the total's tooltip
                      and in the per-model rows — it is detail, and detail does
                      not need a permanent column each. */}
                  <th className="px-4 py-3 font-medium">Est. cost</th>
                  <th className="px-4 py-3 font-medium">Share</th>
                </tr>
              </thead>
              <tbody>
                {ranked.map((entry) => {
                  const isExpanded = expanded.has(entry.client_id);
                                /* PRM-207: expandable whenever there is a breakdown at all. It
                 used to require more than one model, so a client that called a
                 single model could not see *which* — the question is the same
                 either way, and the row simply refused to answer it. */
              const hasBreakdown = entry.by_model.length > 0;
                  return (
                    <Fragment key={entry.client_id}>
                      <tr
                        className={`border-b border-border last:border-0 ${
                          hasBreakdown ? "cursor-pointer hover:bg-background/50" : ""
                        }`}
                        onClick={hasBreakdown ? () => toggleExpanded(entry.client_id) : undefined}
                      >
                        <td className="flex items-center gap-1.5 px-4 py-3 font-medium text-text">
                          {hasBreakdown ? (
                            isExpanded ? (
                              <ChevronDown size={14} className="text-text-muted" />
                            ) : (
                              <ChevronRight size={14} className="text-text-muted" />
                            )
                          ) : (
                            <span className="w-[14px]" />
                          )}
                          {nameByClientId.get(entry.client_id) ?? entry.client_id}
                        </td>
                        <td className="px-4 py-3 text-text-muted">
                          {entry.prompt_tokens.toLocaleString()}
                        </td>
                        <td className="px-4 py-3 text-text-muted">
                          {entry.completion_tokens.toLocaleString()}
                        </td>
                        <td className="px-4 py-3 text-text-muted">
                          {entry.total_tokens.toLocaleString()}
                        </td>
                        <td className="px-4 py-3 text-text-muted">
                          {entry.request_count.toLocaleString()}
                        </td>
                        <td
                          className="px-4 py-3 text-text-muted"
                          title={[
                            `Prompt: ${formatUsdCost(entry.prompt_cost_usd)}`,
                            `Completion: ${formatUsdCost(entry.completion_cost_usd)}`,
                            `Image: ${formatUsdCost(entry.image_cost_usd)}`,
                          ].join("\n")}
                        >
                          {formatUsdCost(entry.estimated_cost_usd)}
                        </td>
                        <td className="px-4 py-3">
                          {/* PRM-207: proportion, because "who is the heavy
                              user" is read off a shape far faster than off a
                              column of numbers. Measured against tokens rather
                              than cost so an unpriced client still appears —
                              otherwise the one model with no price configured
                              would look like no usage at all. */}
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 w-20 overflow-hidden rounded-full bg-background">
                              <div
                                className="h-full rounded-full bg-primary"
                                style={{
                                  width: `${dayTotals.tokens > 0 ? Math.max(2, (entry.total_tokens / dayTotals.tokens) * 100) : 0}%`,
                                }}
                              />
                            </div>
                            <span className="text-xs tabular-nums text-text-muted">
                              {/* "<1%" rather than "0%": a client that used 272
                                  tokens did use the platform, and a bar drawn
                                  beside a zero contradicts itself. */}
                              {dayTotals.tokens === 0
                                ? "—"
                                : entry.total_tokens / dayTotals.tokens < 0.005
                                  ? "<1%"
                                  : `${((entry.total_tokens / dayTotals.tokens) * 100).toFixed(0)}%`}
                            </span>
                          </div>
                        </td>
                      </tr>
                      {isExpanded &&
                        entry.by_model.map((model) => (
                          <tr
                            key={`${entry.client_id}-${model.model_id}`}
                            className="border-b border-border bg-background/30 last:border-0"
                          >
                            <td className="px-4 py-2 pl-10 text-text-muted">{model.model_id}</td>
                            <td className="px-4 py-2 text-text-muted">
                              {model.prompt_tokens.toLocaleString()}
                            </td>
                            <td className="px-4 py-2 text-text-muted">
                              {model.completion_tokens.toLocaleString()}
                            </td>
                            <td className="px-4 py-2 text-text-muted">
                              {model.total_tokens.toLocaleString()}
                            </td>
                            <td className="px-4 py-2 text-text-muted">
                              {model.request_count.toLocaleString()}
                            </td>
                            <td
                              className="px-4 py-2 text-text-muted"
                              title={[
                                `Prompt: ${formatUsdCost(model.prompt_cost_usd)}`,
                                `Completion: ${formatUsdCost(model.completion_cost_usd)}`,
                                `Image: ${formatUsdCost(model.image_cost_usd)}`,
                              ].join("\n")}
                            >
                              {formatUsdCost(model.estimated_cost_usd)}
                            </td>
                            <td className="px-4 py-2" />
                          </tr>
                        ))}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </div>
  );
}
