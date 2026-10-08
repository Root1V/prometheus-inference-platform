import { useDashboardConfig } from "../api/config";
import { useInstances } from "../api/instances";
import { useLiveLimits, type LiveCounter } from "../api/limits";
import { useMetrics } from "../api/metrics";
import { useUsers } from "../api/users";
import { CircuitBadge } from "../components/CircuitBadge";
import { CircuitBreakerForm } from "../components/CircuitBreakerForm";
import { RateLimitsForm } from "../components/RateLimitsForm";
import { Sidebar } from "../components/Sidebar";
import { StatCard } from "../components/StatCard";
import { AlertOctagon, RotateCw } from "lucide-react";
import { useState } from "react";

/**
 * What each counter counts — PRM-230.
 *
 * Spelled out rather than shown as `tpm_in`, because this table is read while
 * something is being refused and "input tokens per minute" is the sentence a
 * reader needs then. The configuration cards above keep the field names: there
 * the name *is* the thing to copy into `.env`.
 */
const DIMENSION_LABELS: Record<LiveCounter["dimension"], string> = {
  rpm: "requests / minute",
  tpm: "tokens / minute",
  tpm_in: "input tokens / minute",
  tpm_out: "output tokens / minute",
  rpd: "requests / day",
  tpd: "tokens / day",
  ipm: "images / minute",
};

const LAYER_CHIP: Record<LiveCounter["layer"], string> = {
  platform: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  client: "bg-primary/10 text-primary",
  endpoint: "bg-background text-text-muted",
};

/**
 * One counter, as a row — PRM-230.
 *
 * The bar is the point of the row. A table of `4 / 3` and `3 / 18` makes the
 * reader do the division that decides which one refused, and they are doing it
 * while an integration is down.
 */
function LiveRow({ row, who }: { row: LiveCounter; who: string }) {
  const percent = row.percent;
  const refusing = percent !== null && percent >= 100;
  const near = percent !== null && percent >= 80 && !refusing;
  return (
    <tr className="border-b border-border last:border-0">
      <td className="whitespace-nowrap px-4 py-3">
        <p className="text-text">{DIMENSION_LABELS[row.dimension]}</p>
        <p className="mt-1 flex items-center gap-1.5 text-xs text-text-muted">
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${LAYER_CHIP[row.layer]}`}
          >
            {row.layer}
          </span>
          {row.endpoint === "*" ? "every endpoint" : row.endpoint}
        </p>
      </td>
      <td className="px-4 py-3">
        <p className="truncate text-text" title={row.identity}>
          {who}
        </p>
        {who !== row.identity && (
          <p className="mt-0.5 truncate font-mono text-[11px] text-text-muted">
            {row.identity}
          </p>
        )}
      </td>
      <td className="whitespace-nowrap px-4 py-3 tabular-nums text-text">
        {row.used.toLocaleString()}
        <span className="text-text-muted">
          {" / "}
          {row.limit === null ? "\u2014" : row.limit.toLocaleString()}
        </span>
        {row.limit_source === "derived" && (
          <span
            className="ml-2 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary"
            title="Nobody set this ceiling — it is the sum of the per-endpoint allowances, computed and enforced. A tier on the Users page replaces it with a chosen number."
          >
            derived
          </span>
        )}
        {row.limit_source === "tier" && (
          <span
            className="ml-2 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary"
            title="This client's tier sets the ceiling, overriding the platform default."
          >
            tier
          </span>
        )}
      </td>
      <td className="w-56 px-4 py-3">
        {percent === null ? (
          <span
            className="text-xs text-text-muted"
            title="This counter is incremented and no check reads it, so nothing here can refuse a request. Shown because a measured, unenforced dimension is worth knowing about — it is not headroom."
          >
            counted, not checked
          </span>
        ) : (
          <div className="flex items-center gap-2">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-background">
              <div
                className={`h-full rounded-full ${
                  refusing ? "bg-red-500" : near ? "bg-amber-500" : "bg-primary"
                }`}
                style={{ width: `${Math.min(100, percent)}%` }}
              />
            </div>
            <span
              className={`w-14 shrink-0 text-right text-xs tabular-nums ${
                refusing
                  ? "font-medium text-red-500"
                  : near
                    ? "text-amber-600 dark:text-amber-400"
                    : "text-text-muted"
              }`}
            >
              {percent}%
            </span>
          </div>
        )}
      </td>
    </tr>
  );
}

/**
 * The live diagnostic — PRM-230.
 *
 * First on the page, because it is the question the page gets opened with:
 * something is being refused, which ceiling did it hit. Three conjoined layers
 * and seven dimensions make that un-deducible from the configuration below —
 * a caller sees one 429 and the reason is whichever counter crossed.
 */
function LiveCeilings() {
  const liveQuery = useLiveLimits();
  const usersQuery = useUsers();
  const [showQuiet, setShowQuiet] = useState(false);
  const [showUnmetered, setShowUnmetered] = useState(false);
  const live = liveQuery.data;

  // An identity is a client_id or a user_id; only the first has a name here,
  // and a raw UUID is not an answer to "who is being limited".
  const names = new Map(
    (usersQuery.data ?? []).map((u) => [
      u.client_id,
      u.label || u.client_name || u.client_id,
    ]),
  );

  if (liveQuery.isError) {
    return (
      <p className="mt-3 rounded-xl border border-border bg-surface px-4 py-3 text-sm text-text-muted">
        Could not read the counters.
      </p>
    );
  }
  if (live && !live.available) {
    return (
      <p className="mt-3 rounded-xl border border-border bg-surface px-4 py-3 text-sm text-text-muted">
        {live.reason}
      </p>
    );
  }

  const rows = live?.rows ?? [];
  // Three groups, because they answer three different questions and the server
  // already sorted them into this order: what is close to refusing, what is
  // merely busy, and what nothing is watching.
  const metered = rows.filter((r) => r.percent !== null);
  const unmetered = rows.filter((r) => r.percent === null);
  const busy = metered.filter((r) => (r.percent ?? 0) >= 1);
  // Five rows even in a quiet minute: a table that collapses to nothing while
  // traffic is flowing reads as broken.
  const visible = busy.length >= 5 ? busy : metered.slice(0, 5);
  const quiet = metered.slice(visible.length);
  const refusing = metered.filter((r) => (r.percent ?? 0) >= 100);
  const shown = showQuiet ? metered : visible;

  return (
    <>
      {refusing.length > 0 && (
        <p className="mt-3 rounded-xl border border-red-500/30 bg-red-500/5 px-4 py-3 text-sm font-medium text-red-500">
          {refusing.length === 1
            ? "One ceiling is refusing requests right now \u2014 the top row."
            : `${refusing.length} ceilings are refusing requests right now \u2014 the top rows.`}
        </p>
      )}
      <div className="mt-3 overflow-x-auto rounded-xl border border-border bg-surface">
        {metered.length === 0 ? (
          <div className="p-10 text-center text-sm text-text-muted">
            {liveQuery.isLoading
              ? "Reading the counters\u2026"
              : /* Not an empty state to apologise for: no counter standing is a
                   minute in which nothing could have been refused. */
                "No counter standing this minute \u2014 nothing is close to a ceiling."}
          </div>
        ) : (
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                <th className="px-4 py-3 font-medium">Ceiling</th>
                <th className="px-4 py-3 font-medium">Who</th>
                <th className="px-4 py-3 font-medium">Used</th>
                <th className="px-4 py-3 font-medium">Headroom</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => (
                <LiveRow
                  key={`${row.dimension}:${row.identity}:${row.endpoint}`}
                  row={row}
                  who={names.get(row.identity) ?? row.identity}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-muted">
        {quiet.length > 0 && (
          <button
            type="button"
            onClick={() => setShowQuiet((v) => !v)}
            className="underline decoration-dotted hover:text-text"
          >
            {showQuiet
              ? "Hide the counters below 1%"
              : `${quiet.length} more below 1% of their ceiling`}
          </button>
        )}
        {unmetered.length > 0 && (
          <button
            type="button"
            onClick={() => setShowUnmetered((v) => !v)}
            className="underline decoration-dotted hover:text-text"
            title="These are incremented and no check reads them, so they cannot refuse anything. Set the matching limit in .env to turn a meter into a ceiling."
          >
            {showUnmetered
              ? "Hide the counters nothing checks"
              : `${unmetered.length} counted and unchecked`}
          </button>
        )}
        {live?.minute_resets_in !== undefined && (
          <span className="tabular-nums">
            minute bucket resets in {live.minute_resets_in}s
          </span>
        )}
      </div>

      {showUnmetered && (
        <div className="mt-2 overflow-x-auto rounded-xl border border-dashed border-border bg-surface">
          {/* Worth its own panel rather than a filter on the table above: these
              rows are a finding about the configuration, not a reading about
              the traffic. PRM-224..226 shipped the meters; a dimension with no
              number set measures faithfully and refuses nothing. */}
          <p className="px-4 pt-3 text-xs text-text-muted">
            Measured, and no ceiling reads them — so none of these can refuse a
            request. Setting the matching limit turns a meter into a ceiling.
          </p>
          <table className="w-full min-w-[720px] text-left text-sm">
            <tbody>
              {unmetered.map((row) => (
                <LiveRow
                  key={`${row.dimension}:${row.identity}:${row.endpoint}`}
                  row={row}
                  who={names.get(row.identity) ?? row.identity}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

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
          {/* PRM-232: this said six of the seventeen were editable and the
              rest needed .env and a restart. All seventeen are editable now,
              so the sentence that existed to admit the gap goes with it. */}
          Rate limits apply in three layers and a request passes all of them.
          Every ceiling below is editable here and applies without a restart; a
          client&rsquo;s own entitlement comes from its tier, set on the Users
          page.
        </p>

        {/* PRM-230: measurements before configuration. The page used to answer
            "what are the limits", and the question it gets opened with is
            "which one is refusing me". */}
        <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-text-muted">
          Right now
        </h2>
        <p className="mt-1 text-sm text-text-muted">
          Every counter standing in this minute — or today, for the daily ones —
          against the ceiling that reads it, closest to refusing first. Counters
          are per-bucket and reset on their own, so a row here is this minute's
          traffic, not a trend. The dashboard's own requests appear under the
          admin endpoint.
        </p>
        <LiveCeilings />

        {/* PRM-232: the cards are the editor now. PRM-229 drew them read-only
            beside a form that edited six of the numbers on them; with all
            seventeen editable that would have been every value twice, and the
            second copy is the one that goes stale. */}
        <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-text-muted">
          Rate limits
        </h2>
        <div className="mt-2">
          <RateLimitsForm />
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
            {/* Not editable here, and the one setting on this page that
                isn't: fail-open vs fail-closed is a deployment decision, not
                a tuning knob. */}
            <span className="text-xs text-text-muted">(.env only)</span>
          </p>
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
