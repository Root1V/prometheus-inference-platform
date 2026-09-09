import { RotateCcw, Save } from "lucide-react";
import { useState } from "react";
import {
  useCircuitBreakerSettings,
  useResetCircuitBreakerSettings,
  useUpdateCircuitBreakerSettings,
  type CircuitBreakerResponse,
  type CircuitBreakerValues,
} from "../api/circuitBreaker";
import { useToast } from "../context/ToastContext";
import { getErrorMessage } from "../lib/errors";

const inputClass =
  "w-32 rounded-lg border border-border bg-background px-2 py-1.5 text-sm text-text focus:border-primary focus:outline-none";

function parseThreshold(raw: string): number | "invalid" {
  const n = Number(raw);
  if (raw.trim() === "" || !Number.isInteger(n) || n < 1) return "invalid";
  return n;
}

function Field({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="block">
      <span className="block text-sm text-text">{label}</span>
      <span className="mb-1.5 block text-xs text-text-muted">{hint}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        inputMode="numeric"
        className={inputClass}
      />
    </label>
  );
}

/**
 * RM-67: circuit-breaker thresholds, editable without a restart. Unlike the
 * rate limits above, these can't just be written to Settings — the backend
 * pool and every breaker it already built keep their own copies, so the
 * server pushes the change into both. A breaker that is currently open stays
 * open; it just recovers on the new timeout.
 */
export function CircuitBreakerForm() {
  const query = useCircuitBreakerSettings();

  if (query.isLoading || !query.data) {
    return (
      <div className="rounded-xl border border-border bg-surface p-5 text-sm text-text-muted">
        Loading circuit-breaker settings…
      </div>
    );
  }

  return <CircuitBreakerFormLoaded key={String(query.data.is_overridden)} data={query.data} />;
}

function CircuitBreakerFormLoaded({ data }: { data: CircuitBreakerResponse }) {
  const update = useUpdateCircuitBreakerSettings();
  const reset = useResetCircuitBreakerSettings();
  const { showToast } = useToast();

  const [failure, setFailure] = useState(String(data.settings.circuit_breaker_failure_threshold));
  const [recovery, setRecovery] = useState(String(data.settings.circuit_breaker_recovery_timeout));
  const [success, setSuccess] = useState(String(data.settings.circuit_breaker_success_threshold));

  const isBusy = update.isPending || reset.isPending;

  function handleSave() {
    const parsed = {
      circuit_breaker_failure_threshold: parseThreshold(failure),
      circuit_breaker_recovery_timeout: parseThreshold(recovery),
      circuit_breaker_success_threshold: parseThreshold(success),
    };
    if (Object.values(parsed).includes("invalid")) {
      showToast("All three thresholds must be whole numbers of 1 or more.", "error");
      return;
    }
    if ((parsed.circuit_breaker_recovery_timeout as number) > data.max_recovery_timeout) {
      showToast(
        `The recovery timeout can't exceed ${data.max_recovery_timeout} seconds.`,
        "error",
      );
      return;
    }
    update.mutate(parsed as CircuitBreakerValues, {
      onSuccess: () =>
        showToast("Circuit-breaker settings saved — applied to running backends", "success"),
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
          <h3 className="text-sm font-medium text-text">Edit thresholds</h3>
          <p className="mt-1 text-xs text-text-muted">
            Applied to every backend immediately, including ones already running — no restart. A
            circuit that's open right now stays open, but recovers on the new timeout rather than
            the one in force when it tripped.
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
          label="Failure threshold"
          hint={`consecutive failures before opening · .env: ${data.env_defaults.circuit_breaker_failure_threshold}`}
          value={failure}
          onChange={setFailure}
        />
        <Field
          label="Recovery timeout"
          hint={`seconds before a probe is allowed · .env: ${data.env_defaults.circuit_breaker_recovery_timeout}`}
          value={recovery}
          onChange={setRecovery}
        />
        <Field
          label="Success threshold"
          hint={`probes that must succeed to close · .env: ${data.env_defaults.circuit_breaker_success_threshold}`}
          value={success}
          onChange={setSuccess}
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
            title="Discard the saved thresholds and go back to what .env configured"
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
