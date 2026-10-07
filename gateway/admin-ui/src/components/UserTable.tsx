import { Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import { cn } from "../lib/cn";
import type { Principal } from "../types/user";
import { UserRow } from "./UserRow";

const COLUMNS = [
  { key: "name", label: "Name", className: "" },
  { key: "identifier", label: "Identifier", className: "" },
  { key: "scopes", label: "Platform scopes", className: "" },
  { key: "models", label: "Model access", className: "" },
  { key: "status", label: "Status", className: "" },
  { key: "actions", label: "", className: "text-right" },
];

type StatusFilter = "all" | "active" | "inactive";

const STATUS_TABS: { key: StatusFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "active", label: "Active" },
  { key: "inactive", label: "Inactive" },
];

/**
 * PRM-201. Search covers the model scopes as well as the name, and that is the
 * point rather than a bonus.
 *
 * The question this page could not answer was "who can reach `fara-7b`" — the
 * grants were visible only as a stack of chips inside each row, so answering it
 * meant reading every row. It is also the question asked right before a grant
 * is added or revoked, which is what this page is for.
 */
function matches(user: Principal, term: string): boolean {
  if (!term) return true;
  const haystack = [
    user.client_name,
    user.client_id,
    user.email ?? "",
    user.role,
    ...user.allowed_scopes,
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(term);
}

export function UserTable({
  users,
  onEdit,
  onBilling,
  onRevealCredential,
}: {
  users: Principal[];
  onEdit: (user: Principal) => void;
  onBilling: (user: Principal) => void;
  onRevealCredential: (clientId: string, secret: string, label: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const term = query.trim().toLowerCase();

  const counts = useMemo(
    () => ({
      all: users.length,
      active: users.filter((u) => u.is_active).length,
      inactive: users.filter((u) => !u.is_active).length,
    }),
    [users],
  );

  const shown = useMemo(
    () =>
      users.filter(
        (u) =>
          matches(u, term) &&
          (status === "all" || (status === "active" ? u.is_active : !u.is_active)),
      ),
    [users, term, status],
  );

  if (users.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
        No users yet — click "Create user" to add one.
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border bg-surface">
      <div className="flex flex-wrap items-center gap-3 border-b border-border p-3">
        <div className="relative min-w-[16rem] flex-1">
          <Search
            size={15}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
            aria-hidden
          />
          <input
            // `text`, not `search`: WebKit draws its own clear button inside a
            // search input, which sat beside ours — two controls, one job, and
            // the native one is unstyleable and absent in Firefox, so the
            // toolbar looked different per browser.
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search name, identifier or model…"
            aria-label="Search users"
            className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-9 text-sm text-text placeholder:text-text-muted focus:border-primary focus:outline-none"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label="Clear search"
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-text-muted hover:bg-surface hover:text-text"
            >
              <X size={14} />
            </button>
          )}
        </div>

        <div
          role="tablist"
          aria-label="Filter by status"
          className="flex items-center gap-1 rounded-lg bg-background p-1"
        >
          {STATUS_TABS.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={status === key}
              onClick={() => setStatus(key)}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                status === key
                  ? "bg-surface text-text shadow-sm"
                  : "text-text-muted hover:text-text",
              )}
            >
              {label}
              <span className="ml-1.5 text-xs text-text-muted">{counts[key]}</span>
            </button>
          ))}
        </div>
      </div>

      {shown.length === 0 ? (
        <div className="p-12 text-center text-sm text-text-muted">
          No user matches <span className="font-medium text-text">“{query}”</span>
          {status !== "all" && ` among ${status} users`}.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                {COLUMNS.map((col) => (
                  <th key={col.key} className={cn("px-4 py-3 font-medium", col.className)}>
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((user) => (
                <UserRow
                  key={user.client_id}
                  user={user}
                  highlight={term || undefined}
                  onEdit={onEdit}
                  onBilling={onBilling}
                  onRevealCredential={onRevealCredential}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {term && shown.length > 0 && (
        <div className="border-t border-border px-4 py-2 text-xs text-text-muted">
          {shown.length} of {users.length} users match “{query}”.
        </div>
      )}
    </div>
  );
}
