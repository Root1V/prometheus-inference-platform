import { useDashboardConfig } from "../api/config";
import { useInstances } from "../api/instances";
import { useMetrics } from "../api/metrics";
import { CircuitBadge } from "../components/CircuitBadge";
import { CircuitBreakerForm } from "../components/CircuitBreakerForm";
import { RateLimitsForm } from "../components/RateLimitsForm";
import { Sidebar } from "../components/Sidebar";
import { StatCard } from "../components/StatCard";
import { AlertOctagon, Gauge, RotateCw, ShieldAlert } from "lucide-react";

export default function Limits() {
  const configQuery = useDashboardConfig();
  const metricsQuery = useMetrics();
  const instancesQuery = useInstances();

  const config = configQuery.data;
  const backends = metricsQuery.data?.backends ?? {};
  /**
   * PRM-223: every running backend, not only the ones with counters.
   *
   * The table listed `metrics.backends`, which exists per backend that has
   * served a request since the gateway started. Measured here: 12 instances
   * ready, 3 rows — nine healthy backends simply had no traffic in the hour
   * since the last restart, and were absent. A model missing from a list reads
   * as a model nobody started, which is the mistake PRM-137 named.
   *
   * It matters more on this table than most: an absent row is not a closed
   * circuit. One says "taking traffic and fine", the other says "the breaker
   * has never been exercised here" — and conflating them is the difference
   * between a working backend and an untested one.
   */
  const rows = (instancesQuery.data?.instances ?? [])
    .filter((instance) => instance.state === "ready")
    .map((instance) => ({
      id: instance.id,
      backend: backends[instance.id] ?? null,
    }))
    .sort((a, b) => Number(Boolean(b.backend)) - Number(Boolean(a.backend)));

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="min-w-0 flex-1 px-8 py-8">
        <h1 className="text-2xl font-semibold text-text">Limits</h1>
        <p className="mt-1 text-sm text-text-muted">
          Current rate-limit and circuit-breaker configuration, and live
          per-model circuit state. Rate limits and circuit-breaker thresholds
          are both editable below and apply without a restart.
        </p>

        <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-text-muted">
          Rate limits
        </h2>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Global RPM"
            value={config ? config.rate_limit_rpm : "—"}
            sub="requests/min per client"
            icon={Gauge}
          />
          <StatCard
            label="Global TPM"
            value={config ? config.rate_limit_tpm.toLocaleString() : "—"}
            sub="tokens/min per client"
            icon={Gauge}
          />
          <StatCard
            label="Chat completions override"
            value={
              config
                ? config.rate_limit_rpm_chat_completions !== null
                  ? `${config.rate_limit_rpm_chat_completions} RPM`
                  : "—"
                : "—"
            }
            sub={
              config?.rate_limit_tpm_chat_completions !== null && config
                ? `${config.rate_limit_tpm_chat_completions.toLocaleString()} TPM`
                : "None configured"
            }
            icon={Gauge}
          />
          <StatCard
            label="On store unavailable"
            value={config ? (config.rate_limit_strict ? "Deny" : "Allow") : "—"}
            // The only card here without a line of its own, and the one that
            // most needed it: it decides what happens to every request when
            // Redis cannot be reached, and "strict" alone is a word, not an
            // answer.
            sub={
              config
                ? config.rate_limit_strict
                  ? "Redis down → every request is refused (strict)"
                  : "Redis down → every request is allowed through (fail-open)"
                : undefined
            }
            tone={config?.rate_limit_strict === false ? "warn" : "neutral"}
            toneReason={
              config?.rate_limit_strict === false
                ? "With the store unreachable the limiter cannot count, so nothing is enforced — traffic passes unmetered until Redis returns."
                : undefined
            }
            icon={ShieldAlert}
          />
        </div>

        <div className="mt-4">
          <RateLimitsForm />
        </div>

        <h2 className="mt-10 text-sm font-medium uppercase tracking-wide text-text-muted">
          Circuit breaker
        </h2>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatCard
            label="Failure threshold"
            value={config ? config.circuit_breaker_failure_threshold : "—"}
            sub="failures before opening"
            icon={AlertOctagon}
          />
          <StatCard
            label="Recovery timeout"
            value={config ? `${config.circuit_breaker_recovery_timeout}s` : "—"}
            sub="before probing again"
            icon={RotateCw}
          />
          <StatCard
            label="Success threshold"
            value={config ? config.circuit_breaker_success_threshold : "—"}
            sub="successes to fully close"
            icon={AlertOctagon}
          />
        </div>

        <div className="mt-4">
          <CircuitBreakerForm />
        </div>

        <h2 className="mt-10 text-sm font-medium uppercase tracking-wide text-text-muted">
          Backend circuit state
        </h2>
        {/* The counters are the gateway process's own, which is why a restart
            empties this table — the same caveat Overview carries, and the
            reason nine backends have no row an hour after one. */}
        <p className="mt-2 text-xs text-text-muted">
          Request counts are process-memory only — they reset when the gateway
          restarts, so a backend with no traffic since then has no circuit state
          yet.
        </p>
        <div className="mt-3 overflow-x-auto rounded-xl border border-border bg-surface">
          {rows.length === 0 ? (
            <div className="p-12 text-center text-text-muted">
              No models running — start one from Instances first.
            </div>
          ) : (
            <table className="w-full min-w-[480px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                  <th className="px-4 py-3 font-medium">Model</th>
                  <th className="px-4 py-3 font-medium">Requests</th>
                  <th className="px-4 py-3 font-medium">Circuit</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ id, backend }) => (
                  <tr key={id} className="border-b border-border last:border-0">
                    <td className="px-4 py-3 font-medium text-text">{id}</td>
                    <td className="px-4 py-3 text-text-muted">
                      {backend ? backend.requests_total.toLocaleString() : "0"}
                    </td>
                    <td className="px-4 py-3">
                      {backend ? (
                        <CircuitBadge state={backend.circuit_state} />
                      ) : (
                        <span
                          className="text-xs text-text-muted"
                          title="Running, but it has served no request since the gateway started — the breaker has never been exercised here, which is not the same as closed."
                        >
                          no traffic yet
                        </span>
                      )}
                    </td>
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
