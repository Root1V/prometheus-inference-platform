import { useDashboardConfig } from "../api/config";
import { useInstances } from "../api/instances";
import { useRateLimits, type LimitLayer } from "../api/limits";
import { useMetrics } from "../api/metrics";
import { CircuitBadge } from "../components/CircuitBadge";
import { CircuitBreakerForm } from "../components/CircuitBreakerForm";
import { RateLimitsForm } from "../components/RateLimitsForm";
import { Sidebar } from "../components/Sidebar";
import { StatCard } from "../components/StatCard";
import { AlertOctagon, RotateCw } from "lucide-react";

/**
 * One layer's ceilings — PRM-229.
 *
 * Names come from the setting itself rather than a second table of labels:
 * `rate_limit_tpm_output` is read by whoever sets `RATE_LIMIT_TPM_OUTPUT`, and
 * a prettier name here would be one more thing to keep in step with `.env`.
 *
 * The `.env` chip marks the eleven PRM-224..228 added. They are live and
 * enforced; they are simply not editable from this page until each has a
 * column on `RateLimitConfig` — the migration PRM-182 said belongs in one
 * change rather than two.
 */
function LimitLayerCard({ layer }: { layer: LimitLayer }) {
  // Anything in force, derived or chosen — an enforced ceiling is not absent
  // just because nobody typed it.
  const inForce = layer.fields.filter((f) => f.source !== "unset");
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
        {layer.layer}
      </p>
      <p className="mt-1 text-sm text-text">{layer.what}</p>
      {inForce.length === 0 ? (
        <p className="mt-3 text-xs text-text-muted">
          {/* Absent is not zero. An unset platform ceiling means the platform
              is bounded only by the sum of its consumers — a real state, not
              a missing one. */}
          Nothing set — this layer refuses nothing today.
        </p>
      ) : (
        <div className="mt-3 space-y-1.5">
          {inForce.map((f) => (
            <div key={f.field} className="flex items-baseline gap-2 text-sm">
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-text-muted">
                {f.field.replace("rate_limit_", "")}
              </span>
              <span className="shrink-0 tabular-nums text-text">
                {f.value?.toLocaleString()}
              </span>
              {f.source === "derived" ? (
                <span
                  className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary"
                  title="Nobody set this — it is the sum of the per-endpoint allowances, computed and enforced. Put the client on a tier to choose a real number."
                >
                  derived
                </span>
              ) : (
                !f.editable && (
                  <span
                    className="shrink-0 rounded bg-background px-1.5 py-0.5 text-[10px] font-medium text-text-muted"
                    title="Live and enforced, but set through .env and a restart — this page cannot edit it yet."
                  >
                    .env
                  </span>
                )
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Limits() {
  const configQuery = useDashboardConfig();
  const metricsQuery = useMetrics();
  const limitsQuery = useRateLimits();
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
          {/* PRM-229: "editable below" was true of six fields and is now true
              of six out of seventeen, so saying it plainly beats letting a
              reader discover the other eleven by not finding them. */}
          Rate limits apply in three layers and a request passes all of them.
          The form below edits the endpoint layer and applies without a restart;
          the rest are set through .env, and a client's own ceiling comes from
          its tier on the Users page.
        </p>

        {/* PRM-229: three layers, because a request passes all three.
            This was four cards drawn from one of them, and the one labelled
            "requests/min per client" is the per-*endpoint* value — so the
            page taught a number that is not what a client may consume. */}
        <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-text-muted">
          Rate limits
        </h2>
        <p className="mt-1 text-sm text-text-muted">
          A request passes every layer that applies, not whichever is most
          specific. The narrowest ceiling it meets is the one that stops it.
        </p>
        <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-3">
          {(limitsQuery.data?.layers ?? []).map((layer) => (
            <LimitLayerCard key={layer.layer} layer={layer} />
          ))}
        </div>

        <div className="mt-4 rounded-xl border border-border bg-surface px-4 py-3">
          <p className="flex flex-wrap items-baseline gap-x-2 text-sm">
            <span className="font-medium text-text">
              On store unavailable:{" "}
              {config ? (config.rate_limit_strict ? "Deny" : "Allow") : "—"}
            </span>
            <span className="text-text-muted">
              {config?.rate_limit_strict === false
                ? "Redis down → every request passes unmetered, because the limiter cannot count."
                : "Redis down → every request is refused, across all three layers."}
            </span>
          </p>
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
