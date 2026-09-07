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
          <label className="flex items-center gap-2 text-sm text-text-muted">
            Day
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="rounded-lg border border-border bg-surface px-3 py-1.5 text-text"
            />
          </label>
        </div>

        <div className="mt-4 flex flex-wrap items-end gap-3 rounded-xl border border-border bg-surface p-4">
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
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                  <th className="px-4 py-3 font-medium">Client</th>
                  <th className="px-4 py-3 font-medium">Prompt tokens</th>
                  <th className="px-4 py-3 font-medium">Completion tokens</th>
                  <th className="px-4 py-3 font-medium">Total tokens</th>
                  <th className="px-4 py-3 font-medium">Requests</th>
                  <th className="px-4 py-3 font-medium">Est. cost</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => {
                  const isExpanded = expanded.has(entry.client_id);
                  const hasBreakdown = entry.by_model.length > 1;
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
                        <td className="px-4 py-3 text-text-muted">
                          {formatUsdCost(entry.estimated_cost_usd)}
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
                            <td className="px-4 py-2 text-text-muted">
                              {formatUsdCost(model.estimated_cost_usd)}
                            </td>
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
