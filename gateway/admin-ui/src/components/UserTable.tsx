import type { Principal } from "../types/user";
import { UserRow } from "./UserRow";

const COLUMNS = ["Name", "Auth method", "Identifier", "Role", "Scopes", "Models", "Status", "Actions"];

export function UserTable({
  users,
  onEdit,
  onRevealCredential,
}: {
  users: Principal[];
  onEdit: (user: Principal) => void;
  onRevealCredential: (clientId: string, secret: string, label: string) => void;
}) {
  if (users.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
        No users yet — click "Create user" to add one.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface">
      <table className="w-full min-w-[900px] text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
            {COLUMNS.map((col) => (
              <th key={col} className="px-4 py-3 font-medium">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            <UserRow key={user.client_id} user={user} onEdit={onEdit} onRevealCredential={onRevealCredential} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
