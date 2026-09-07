import { useSessions } from "../api/sessions";
import { useUsers } from "../api/users";
import { Sidebar } from "../components/Sidebar";
import { getErrorMessage } from "../lib/errors";
import { formatAgo } from "../lib/format";

const CONNECTION_LABEL: Record<string, string> = {
  dashboard: "Dashboard",
  api: "API / SDK",
  other: "Other",
};

export default function Sessions() {
  const sessionsQuery = useSessions();
  const usersQuery = useUsers();

  const users = usersQuery.data ?? [];
  const nameByClientId = new Map(users.map((u) => [u.client_id, u.client_name]));
  const sessions = sessionsQuery.data ?? [];

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="min-w-0 flex-1 px-8 py-8">
        <h1 className="text-2xl font-semibold text-text">Sessions</h1>
        <p className="mt-1 text-sm text-text-muted">
          Clients active in the last 15 minutes — a last-seen approximation from request
          traffic, not a real connection registry. The web chat UI's own cookie-based
          sessions aren't visible here.
        </p>

        <div className="mt-6 overflow-x-auto rounded-xl border border-border bg-surface">
          {sessionsQuery.isLoading ? (
            <div className="p-12 text-center text-text-muted">Loading sessions…</div>
          ) : sessionsQuery.isError ? (
            <div className="p-12 text-center text-red-600">{getErrorMessage(sessionsQuery.error)}</div>
          ) : sessions.length === 0 ? (
            <div className="p-12 text-center text-text-muted">No active sessions right now.</div>
          ) : (
            <table className="w-full min-w-[560px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                  <th className="px-4 py-3 font-medium">Client</th>
                  <th className="px-4 py-3 font-medium">Connection</th>
                  <th className="px-4 py-3 font-medium">Last active</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((session) => (
                  <tr key={session.client_id} className="border-b border-border last:border-0">
                    <td className="px-4 py-3 font-medium text-text">
                      {nameByClientId.get(session.client_id) ?? session.client_id}
                    </td>
                    <td className="px-4 py-3 text-text-muted">
                      {CONNECTION_LABEL[session.connection_type] ?? session.connection_type}
                    </td>
                    <td className="px-4 py-3 text-text-muted">{formatAgo(session.last_seen_ago_s)}</td>
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
