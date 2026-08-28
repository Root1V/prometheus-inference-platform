import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";
import { useUsage } from "../api/usage";
import { useUsers } from "../api/users";
import { Sidebar } from "../components/Sidebar";
import { getErrorMessage } from "../lib/errors";
import { formatUsdCost } from "../lib/format";

export default function Usage() {
  const [date, setDate] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const usageQuery = useUsage(date || undefined);
  const usersQuery = useUsers();

  const users = usersQuery.data ?? [];
  const nameByClientId = new Map(users.map((u) => [u.client_id, u.client_name]));
  const entries = usageQuery.data?.data ?? [];

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
      <main className="flex-1 px-8 py-8">
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
