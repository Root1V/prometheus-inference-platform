import { useState } from "react";
import { Pencil, Plus, Trash2, Users } from "lucide-react";
import {
  useDeleteRateLimitTier,
  useRateLimitTiers,
  useUpsertRateLimitTier,
} from "../api/billing";
import type { RateLimitTier } from "../types/billing";
import { useToast } from "../context/ToastContext";
import { getErrorMessage } from "../lib/errors";
import { cn } from "../lib/cn";

/** The seven ceilings a tier may state, in the order the Limits page reads. */
const DIMENSIONS = [
  { key: "rpm", label: "RPM", hint: "Requests per minute, across every endpoint" },
  { key: "tpm", label: "TPM", hint: "Tokens per minute, across every endpoint" },
  { key: "tpm_input", label: "TPM in", hint: "Prompt tokens per minute" },
  { key: "tpm_output", label: "TPM out", hint: "Completion tokens per minute" },
  { key: "rpd", label: "RPD", hint: "Requests per day" },
  { key: "tpd", label: "TPD", hint: "Tokens per day" },
  { key: "ipm", label: "IPM", hint: "Images per minute" },
] as const;

const inputClass =
  "w-full rounded-lg border border-border bg-background px-2 py-1.5 text-sm tabular-nums text-text focus:border-primary focus:outline-none";

/**
 * PRM-256: the tier catalogue, editable where the operator already is.
 *
 * `PUT /admin/api/limits/tiers/{name}` has existed since PRM-228 and nothing on
 * the dashboard reached it — the page only read the catalogue to fill the
 * assignment picker. So "tell us the ceiling you need and we will configure it"
 * meant an operator asking someone with a terminal, which is not a policy a
 * platform can offer.
 *
 * The client count is here for a measured reason. A tier is shared: raising
 * `scale` for the client who asked for it raised it for another who had not,
 * and nothing on screen would have said so before pressing save.
 */
export function RateLimitTiers() {
  const { data: tiers, isLoading } = useRateLimitTiers();
  const upsert = useUpsertRateLimitTier();
  const remove = useDeleteRateLimitTier();
  const { showToast } = useToast();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");

  const startEdit = (tier: RateLimitTier | null) => {
    setEditing(tier ? tier.name : "__new__");
    setDraftName(tier ? tier.name : "");
    setDraftDescription(tier?.description ?? "");
    setDraft(
      Object.fromEntries(
        DIMENSIONS.map(({ key }) => [key, tier?.[key] == null ? "" : String(tier[key])]),
      ),
    );
  };

  const save = () => {
    const name = draftName.trim();
    if (!name) {
      showToast("A tier needs a name.", "error");
      return;
    }
    // An empty box means "no opinion on this dimension" — null, which the
    // endpoint reads as the platform default rather than as unlimited.
    const values: Record<string, number | string | null> = {
      description: draftDescription.trim() || null,
    };
    for (const { key, label } of DIMENSIONS) {
      const raw = (draft[key] ?? "").trim();
      if (raw === "") {
        values[key] = null;
        continue;
      }
      const parsed = Number(raw);
      if (!Number.isFinite(parsed) || parsed < 0) {
        showToast(`${label} must be a number, or empty for the platform default.`, "error");
        return;
      }
      values[key] = Math.round(parsed);
    }
    upsert.mutate(
      { name, values },
      {
        onSuccess: () => {
          showToast(`Tier “${name}” saved.`, "success");
          setEditing(null);
        },
        onError: (error: unknown) => showToast(getErrorMessage(error), "error"),
      },
    );
  };

  const destroy = (tier: RateLimitTier) => {
    if (tier.client_count > 0) {
      showToast(
        `“${tier.name}” still governs ${tier.client_count} client${tier.client_count === 1 ? "" : "s"} — move them first.`,
        "error",
      );
      return;
    }
    remove.mutate(tier.name, {
      onSuccess: () => showToast(`Tier “${tier.name}” deleted.`, "success"),
      onError: (error: unknown) => showToast(getErrorMessage(error), "error"),
    });
  };

  if (isLoading) {
    return <div className="rounded-xl border border-border bg-surface p-8 text-sm text-text-muted">Loading tiers…</div>;
  }

  const rows = tiers ?? [];

  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <p className="text-xs text-text-muted">
          A tier is what one consumer may spend across every endpoint. An empty
          box is the platform default for that dimension, not “unlimited”.
        </p>
        <button
          type="button"
          onClick={() => startEdit(null)}
          className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground transition hover:opacity-90"
        >
          <Plus size={14} />
          New tier
        </button>
      </div>

      <table className="w-full min-w-[820px] text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
            <th className="px-4 py-3 font-medium">Tier</th>
            <th className="px-4 py-3 font-medium">On it</th>
            {DIMENSIONS.map((d) => (
              <th key={d.key} className="cursor-help px-3 py-3 text-right font-medium" title={d.hint}>
                {d.label}
              </th>
            ))}
            <th className="px-4 py-3" />
          </tr>
        </thead>
        <tbody>
          {editing === "__new__" && (
            <TierForm
              draftName={draftName}
              setDraftName={setDraftName}
              draftDescription={draftDescription}
              setDraftDescription={setDraftDescription}
              draft={draft}
              setDraft={setDraft}
              onSave={save}
              onCancel={() => setEditing(null)}
              saving={upsert.isPending}
              isNew
            />
          )}
          {rows.map((tier) =>
            editing === tier.name ? (
              <TierForm
                key={tier.name}
                draftName={draftName}
                setDraftName={setDraftName}
                draftDescription={draftDescription}
                setDraftDescription={setDraftDescription}
                draft={draft}
                setDraft={setDraft}
                onSave={save}
                onCancel={() => setEditing(null)}
                saving={upsert.isPending}
                clientCount={tier.client_count}
              />
            ) : (
              <tr key={tier.name} className="border-b border-border last:border-0">
                <td className="px-4 py-3">
                  <p className="font-mono text-text">{tier.name}</p>
                  {tier.description && (
                    <p className="mt-0.5 text-xs text-text-muted">{tier.description}</p>
                  )}
                </td>
                <td className="px-4 py-3">
                  <span
                    title={
                      tier.client_count === 0
                        ? "No client is on this tier — editing it changes nothing today."
                        : "Editing this tier changes the ceiling for every one of them."
                    }
                    className={cn(
                      "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs tabular-nums",
                      tier.client_count > 0
                        ? "bg-background text-text"
                        : "text-text-muted",
                    )}
                  >
                    <Users size={12} />
                    {tier.client_count}
                  </span>
                </td>
                {DIMENSIONS.map(({ key }) => (
                  <td key={key} className="px-3 py-3 text-right tabular-nums text-text-muted">
                    {tier[key] == null ? (
                      <span title="Platform default">—</span>
                    ) : (
                      tier[key]!.toLocaleString()
                    )}
                  </td>
                ))}
                <td className="whitespace-nowrap px-4 py-3 text-right">
                  <button
                    type="button"
                    onClick={() => startEdit(tier)}
                    aria-label={`Edit tier ${tier.name}`}
                    className="rounded p-1.5 text-text-muted transition hover:bg-background hover:text-text"
                  >
                    <Pencil size={14} />
                  </button>
                  <button
                    type="button"
                    onClick={() => destroy(tier)}
                    aria-label={`Delete tier ${tier.name}`}
                    className="rounded p-1.5 text-text-muted transition hover:bg-background hover:text-red-600"
                  >
                    <Trash2 size={14} />
                  </button>
                </td>
              </tr>
            ),
          )}
        </tbody>
      </table>
    </div>
  );
}

function TierForm({
  draftName,
  setDraftName,
  draftDescription,
  setDraftDescription,
  draft,
  setDraft,
  onSave,
  onCancel,
  saving,
  isNew = false,
  clientCount,
}: {
  draftName: string;
  setDraftName: (v: string) => void;
  draftDescription: string;
  setDraftDescription: (v: string) => void;
  draft: Record<string, string>;
  setDraft: (v: Record<string, string>) => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
  isNew?: boolean;
  clientCount?: number;
}) {
  return (
    <tr className="border-b border-border bg-background/40 last:border-0 align-top">
      <td className="px-4 py-3">
        <input
          value={draftName}
          onChange={(e) => setDraftName(e.target.value)}
          disabled={!isNew}
          placeholder="custom"
          aria-label="Tier name"
          className={cn(inputClass, "font-mono", !isNew && "opacity-60")}
        />
        <input
          value={draftDescription}
          onChange={(e) => setDraftDescription(e.target.value)}
          placeholder="What this tier is for"
          aria-label="Tier description"
          className={cn(inputClass, "mt-1.5 text-xs")}
        />
      </td>
      <td className="px-4 py-3">
        {clientCount != null && clientCount > 0 && (
          <p className="text-xs text-amber-700 dark:text-amber-400">
            changes {clientCount} client{clientCount === 1 ? "" : "s"}
          </p>
        )}
      </td>
      {DIMENSIONS.map(({ key, label }) => (
        <td key={key} className="px-2 py-3">
          <input
            value={draft[key] ?? ""}
            onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
            placeholder="—"
            inputMode="numeric"
            aria-label={label}
            className={cn(inputClass, "text-right")}
          />
        </td>
      ))}
      <td className="whitespace-nowrap px-4 py-3 text-right">
        <button
          type="button"
          onClick={onSave}
          disabled={saving}
          className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="ml-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-text-muted transition hover:text-text"
        >
          Cancel
        </button>
      </td>
    </tr>
  );
}
