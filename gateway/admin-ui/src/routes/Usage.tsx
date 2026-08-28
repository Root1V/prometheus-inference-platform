import { useUsage } from "../api/usage";
import { useUsers } from "../api/users";
import { Sidebar } from "../components/Sidebar";
import { getErrorMessage } from "../lib/errors";

export default function Usage() {
  const usageQuery = useUsage();
  const usersQuery = useUsers();

  const users = usersQuery.data ?? [];
  const nameByClientId = new Map(users.map((u) => [u.client_id, u.client_name]));
  const entries = usageQuery.data?.data ?? [];

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="flex-1 px-8 py-8">
        <h1 className="text-2xl font-semibold text-text">Usage</h1>
        <p className="mt-1 text-sm text-text-muted">
          Token usage per client for {usageQuery.data?.window ?? "today"} (UTC). No historical
          trend or per-model breakdown yet — see docs/roadmap.md RM-32.
        </p>

        <div className="mt-6 overflow-x-auto rounded-xl border border-border bg-surface">
          {usageQuery.isLoading ? (
            <div className="p-12 text-center text-text-muted">Loading usage…</div>
          ) : usageQuery.isError ? (
            <div className="p-12 text-center text-red-600">{getErrorMessage(usageQuery.error)}</div>
          ) : entries.length === 0 ? (
            <div className="p-12 text-center text-text-muted">No usage recorded for today yet.</div>
          ) : (
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                  <th className="px-4 py-3 font-medium">Client</th>
                  <th className="px-4 py-3 font-medium">Prompt tokens</th>
                  <th className="px-4 py-3 font-medium">Completion tokens</th>
                  <th className="px-4 py-3 font-medium">Total tokens</th>
                  <th className="px-4 py-3 font-medium">Requests</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr key={entry.client_id} className="border-b border-border last:border-0">
                    <td className="px-4 py-3 font-medium text-text">
                      {nameByClientId.get(entry.client_id) ?? entry.client_id}
                    </td>
                    <td className="px-4 py-3 text-text-muted">{entry.prompt_tokens.toLocaleString()}</td>
                    <td className="px-4 py-3 text-text-muted">{entry.completion_tokens.toLocaleString()}</td>
                    <td className="px-4 py-3 text-text-muted">{entry.total_tokens.toLocaleString()}</td>
                    <td className="px-4 py-3 text-text-muted">{entry.request_count.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </div>
  );
}
