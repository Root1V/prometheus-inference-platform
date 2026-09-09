import { RotateCcw, Save } from "lucide-react";
import { useState } from "react";
import {
  useRateLimits,
  useResetRateLimits,
  useUpdateRateLimits,
  type RateLimitValues,
  type RateLimitsResponse,
} from "../api/limits";
import { useToast } from "../context/ToastContext";
import { getErrorMessage } from "../lib/errors";

const inputClass =
  "w-32 rounded-lg border border-border bg-background px-2 py-1.5 text-sm text-text focus:border-primary focus:outline-none";

/** Blank is valid for the per-endpoint overrides — it means "use the global". */
function parseLimit(raw: string): number | null | "invalid" {
  if (raw.trim() === "") return null;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1) return "invalid";
  return n;
}

function Field({
  label,
  hint,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="block text-sm text-text">{label}</span>
      <span className="mb-1.5 block text-xs text-text-muted">{hint}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        inputMode="numeric"
        placeholder={placeholder}
        className={inputClass}
      />
    </label>
  );
}

/**
 * RM-56: rate limits used to be .env-only, needing a gateway restart to
 * change. The gateway re-reads them per request, so a save here applies to
 * the very next request; the DB row behind it is what survives a restart.
 */
export function RateLimitsForm() {
  const limitsQuery = useRateLimits();

  if (limitsQuery.isLoading || !limitsQuery.data) {
    return (
      <div className="rounded-xl border border-border bg-surface p-5 text-sm text-text-muted">
        Loading rate limits…
      </div>
    );
  }

  return <RateLimitsFormLoaded key={String(limitsQuery.data.is_overridden)} data={limitsQuery.data} />;
}

function RateLimitsFormLoaded({ data }: { data: RateLimitsResponse }) {
  const update = useUpdateRateLimits();
  const reset = useResetRateLimits();
  const { showToast } = useToast();

  const asText = (v: number | null) => (v === null ? "" : String(v));
  const [rpm, setRpm] = useState(asText(data.limits.rate_limit_rpm));
  const [tpm, setTpm] = useState(asText(data.limits.rate_limit_tpm));
  const [chatRpm, setChatRpm] = useState(asText(data.limits.rate_limit_rpm_chat_completions));
  const [chatTpm, setChatTpm] = useState(asText(data.limits.rate_limit_tpm_chat_completions));
  const [adminRpm, setAdminRpm] = useState(asText(data.limits.rate_limit_rpm_admin));
  const [adminTpm, setAdminTpm] = useState(asText(data.limits.rate_limit_tpm_admin));

  const isBusy = update.isPending || reset.isPending;

  function handleSave() {
    const parsed = {
      rate_limit_rpm: parseLimit(rpm),
      rate_limit_tpm: parseLimit(tpm),
      rate_limit_rpm_chat_completions: parseLimit(chatRpm),
      rate_limit_tpm_chat_completions: parseLimit(chatTpm),
      rate_limit_rpm_admin: parseLimit(adminRpm),
      rate_limit_tpm_admin: parseLimit(adminTpm),
    };
    if (Object.values(parsed).includes("invalid")) {
      showToast("Limits must be whole numbers of 1 or more, or blank.", "error");
      return;
    }
    if (parsed.rate_limit_rpm === null || parsed.rate_limit_tpm === null) {
      showToast("The global RPM and TPM limits are required.", "error");
      return;
    }
    update.mutate(parsed as RateLimitValues, {
      onSuccess: () => showToast("Rate limits saved — applied to the next request", "success"),
      onError: (e) => showToast(getErrorMessage(e), "error"),
    });
  }

  function handleReset() {
    reset.mutate(undefined, {
      onSuccess: () => showToast("Reverted to the values from .env", "success"),
      onError: (e) => showToast(getErrorMessage(e), "error"),
    });
  }

  return (
    <div className="rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium text-text">Edit limits</h3>
          <p className="mt-1 text-xs text-text-muted">
            Applied to the very next request — no restart. Per-endpoint fields are optional:
            blank means that endpoint uses the global limit. The admin API can't go below{" "}
            {data.min_admin_rpm} RPM, so a mistake here can never lock you out of this page.
          </p>
        </div>
        <span
          className={
            "shrink-0 rounded-full px-2 py-0.5 text-xs " +
            (data.is_overridden ? "bg-primary/10 text-primary" : "bg-background text-text-muted")
          }
        >
          {data.is_overridden ? "custom" : "from .env"}
        </span>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field
          label="Global RPM"
          hint={`requests/min per client · .env: ${data.env_defaults.rate_limit_rpm}`}
          value={rpm}
          onChange={setRpm}
        />
        <Field
          label="Global TPM"
          hint={`tokens/min per client · .env: ${data.env_defaults.rate_limit_tpm.toLocaleString()}`}
          value={tpm}
          onChange={setTpm}
        />
        <div />
        <Field
          label="Chat completions RPM"
          hint={`overrides the global for /v1/chat/completions · .env: ${
            data.env_defaults.rate_limit_rpm_chat_completions ?? "unset"
          }`}
          value={chatRpm}
          onChange={setChatRpm}
          placeholder="use global"
        />
        <Field
          label="Chat completions TPM"
          hint={`.env: ${data.env_defaults.rate_limit_tpm_chat_completions ?? "unset"}`}
          value={chatTpm}
          onChange={setChatTpm}
          placeholder="use global"
        />
        <div />
        <Field
          label="Admin API RPM"
          hint={`this dashboard's own budget · .env: ${
            data.env_defaults.rate_limit_rpm_admin ?? "unset"
          }`}
          value={adminRpm}
          onChange={setAdminRpm}
          placeholder="use global"
        />
        <Field
          label="Admin API TPM"
          hint={`.env: ${data.env_defaults.rate_limit_tpm_admin ?? "unset"}`}
          value={adminTpm}
          onChange={setAdminTpm}
          placeholder="use global"
        />
      </div>

      <div className="mt-5 flex items-center gap-3">
        <button
          type="button"
          onClick={handleSave}
          disabled={isBusy}
          className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Save size={14} />
          {update.isPending ? "Saving…" : "Save"}
        </button>
        {data.is_overridden && (
          <button
            type="button"
            onClick={handleReset}
            disabled={isBusy}
            title="Discard the saved limits and go back to what .env configured"
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:bg-background disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RotateCcw size={14} />
            Reset to .env
          </button>
        )}
      </div>
    </div>
  );
}
