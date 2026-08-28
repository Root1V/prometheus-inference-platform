import { useDashboardConfig } from "../api/config";
import { useMetrics } from "../api/metrics";
import { CircuitBadge } from "../components/CircuitBadge";
import { Sidebar } from "../components/Sidebar";
import { StatCard } from "../components/StatCard";
import { AlertOctagon, Gauge, RotateCw, ShieldAlert } from "lucide-react";

export default function Limits() {
  const configQuery = useDashboardConfig();
  const metricsQuery = useMetrics();

  const config = configQuery.data;
  const backendEntries = Object.entries(metricsQuery.data?.backends ?? {});

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="flex-1 px-8 py-8">
        <h1 className="text-2xl font-semibold text-text">Limits</h1>
        <p className="mt-1 text-sm text-text-muted">
          Current rate-limit and circuit-breaker configuration, and live per-model circuit
          state. Read-only — these values come from the gateway's own config, not something
          editable here.
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
            value={config ? (config.rate_limit_strict ? "Deny (strict)" : "Allow (fail-open)") : "—"}
            icon={ShieldAlert}
          />
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

        <h2 className="mt-10 text-sm font-medium uppercase tracking-wide text-text-muted">
          Backend circuit state
        </h2>
        <div className="mt-3 overflow-x-auto rounded-xl border border-border bg-surface">
          {backendEntries.length === 0 ? (
            <div className="p-12 text-center text-text-muted">No backend traffic recorded yet.</div>
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
                {backendEntries.map(([backendId, backend]) => (
                  <tr key={backendId} className="border-b border-border last:border-0">
                    <td className="px-4 py-3 font-medium text-text">{backendId}</td>
                    <td className="px-4 py-3 text-text-muted">{backend.requests_total.toLocaleString()}</td>
                    <td className="px-4 py-3">
                      <CircuitBadge state={backend.circuit_state} />
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
