import { Plus } from "lucide-react";
import { useState } from "react";
import { useUsers } from "../api/users";
import { CreateUserModal } from "../components/CreateUserModal";
import { CredentialRevealDialog } from "../components/CredentialRevealDialog";
import { Sidebar } from "../components/Sidebar";
import { UserTable } from "../components/UserTable";
import type { CreatePrincipalResponse, Principal } from "../types/user";

const EMPTY_USERS: Principal[] = [];

type ModalState = { mode: "create" } | { mode: "edit"; user: Principal } | null;
type RevealState = { clientId: string; secret: string; label: string } | null;

export default function Users() {
  const usersQuery = useUsers();
  const [modal, setModal] = useState<ModalState>(null);
  const [reveal, setReveal] = useState<RevealState>(null);

  const users = usersQuery.data ?? EMPTY_USERS;

  const handleCreated = (data: CreatePrincipalResponse) => {
    if (!data.client_secret) return;
    setReveal({
      clientId: data.client_id,
      secret: data.client_secret,
      label: data.auth_method === "oauth2" ? "Client secret" : "Password",
    });
  };

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="min-w-0 flex-1 px-8 py-8">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-text">Users</h1>
          <button
            type="button"
            onClick={() => setModal({ mode: "create" })}
            className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            <Plus size={16} />
            Create user
          </button>
        </div>

        <div className="mt-6">
          {usersQuery.isLoading ? (
            <div className="rounded-xl border border-border bg-surface p-12 text-center text-text-muted">
              Loading users…
            </div>
          ) : (
            <UserTable
              users={users}
              onEdit={(user) => setModal({ mode: "edit", user })}
              onRevealCredential={(clientId, secret, label) => setReveal({ clientId, secret, label })}
            />
          )}
        </div>
      </main>

      <CreateUserModal
        key={modal?.mode === "edit" ? modal.user.client_id : "create"}
        open={modal !== null}
        editing={modal?.mode === "edit" ? modal.user : null}
        onClose={() => setModal(null)}
        onCreated={handleCreated}
      />
      <CredentialRevealDialog
        open={reveal !== null}
        clientId={reveal?.clientId ?? null}
        secret={reveal?.secret ?? null}
        label={reveal?.label ?? ""}
        onClose={() => setReveal(null)}
      />
    </div>
  );
}
