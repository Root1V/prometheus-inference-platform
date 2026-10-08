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

const PLATFORM_IDENTITY = "*platform*";

/** Shared colouring: a counter is refusing, near, or fine. */
function toneOf(percent: number | null) {
  if (percent === null)
    return { bar: "bg-text-muted/30", text: "text-text-muted" };
  if (percent >= 100)
    return { bar: "bg-red-500", text: "font-medium text-red-500" };
  if (percent >= 80)
    return { bar: "bg-amber-500", text: "text-amber-600 dark:text-amber-400" };
  return { bar: "bg-primary", text: "text-text-muted" };
}

/**
 * One counter as used / limit with a bar — PRM-233.
 *
 * The rollups and the detail table show the same quantity, so they show it
 * the same way. A second rendering of "how full is this" would be a second
 * thing to keep honest.
 */
function Meter({
  row,
  width = "w-28",
}: {
  row: LiveCounter | undefined;
  width?: string;
}) {
  if (!row) return <span className="text-xs text-text-muted">—</span>;
  const tone = toneOf(row.percent);
  return (
    <div>
      <p className="text-sm tabular-nums text-text">
        {row.used.toLocaleString()}
        <span className="text-text-muted">
          {" / "}
          {row.limit === null ? "\u2014" : row.limit.toLocaleString()}
        </span>
      </p>
      <div className="mt-1 flex items-center gap-2">
        <div
          className={`h-1 ${width} overflow-hidden rounded-full bg-background`}
        >
          {row.percent !== null && (
            <div
              className={`h-full rounded-full ${tone.bar}`}
              style={{ width: `${Math.min(100, row.percent)}%` }}
            />
          )}
        </div>
        <span className={`text-[11px] tabular-nums ${tone.text}`}>
          {row.percent === null ? "no ceiling" : `${row.percent}%`}
        </span>
      </div>
    </div>
  );
}

/**
 * The live diagnostic — PRM-230, answering "how close" as of PRM-233.
 *
 * First on the page, because it is the question the page gets opened with:
 * something is being refused, which ceiling did it hit. PRM-230 listed every
 * counter, which answers that once something is already at its ceiling and
 * not the question before it — *who is about to be*. With fourteen clients
 * across six endpoints and seven dimensions, that answer is not in a list of
 * a hundred rows; it is in two roll-ups, one per consumer and one for the
 * whole platform. The list is still here, one click down, because when a
 * 429 has already happened the exact counter is what you want.
 */
function LiveCeilings() {
  const liveQuery = useLiveLimits();
  const usersQuery = useUsers();
  const [showDetail, setShowDetail] = useState(false);
  const live = liveQuery.data;

  // An identity is a client_id or a user_id; only the first has a name here,
  // and a raw UUID is not an answer to "who is being limited".
  const names = new Map(
    (usersQuery.data ?? []).map((u) => [
      u.client_id,
      u.label || u.client_name || u.client_id,
    ]),
  );

  if (liveQuery.isError || (live && !live.available)) {
    return (
      <p className="mt-3 rounded-xl border border-border bg-surface px-4 py-3 text-sm text-text-muted">
        {live?.reason ??
          "Could not read the counters — this gateway may be running a build without /admin/api/limits/live."}
      </p>
    );
  }

  const rows = live?.rows ?? [];
  const platformRpm = rows.find(
    (r) => r.layer === "platform" && r.dimension === "rpm",
  );
  const platformTpm = rows.find(
    (r) => r.layer === "platform" && r.dimension === "tpm",
  );

  // One entry per consumer, with its own ceiling and its worst counter —
  // which are two different facts. A client can sit at 3% of its client-wide
  // RPM while one endpoint of its traffic is at 98%, and the second number is
  // the one that is about to produce a 429.
  const byIdentity = new Map<string, LiveCounter[]>();
  for (const row of rows) {
    if (row.identity === PLATFORM_IDENTITY) continue;
    const list = byIdentity.get(row.identity);
    if (list) list.push(row);
    else byIdentity.set(row.identity, [row]);
  }
  const clients = [...byIdentity.entries()]
    .map(([identity, own]) => {
      const metered = own.filter((r) => r.percent !== null);
      return {
        identity,
        rpm: own.find((r) => r.layer === "client" && r.dimension === "rpm"),
        tpm: own.find((r) => r.layer === "client" && r.dimension === "tpm"),
        worst: metered.length
          ? metered.reduce((a, b) =>
              (b.percent ?? 0) > (a.percent ?? 0) ? b : a,
            )
          : undefined,
      };
    })
    .sort((a, b) => (b.worst?.percent ?? -1) - (a.worst?.percent ?? -1));

  const refusing = rows.filter((r) => (r.percent ?? 0) >= 100);
  const quiet = liveQuery.isLoading
    ? "Reading the counters\u2026"
    : "No counter standing this minute \u2014 nothing is close to a ceiling.";

  return (
    <>
      {refusing.length > 0 && (
        <p className="mt-3 rounded-xl border border-red-500/30 bg-red-500/5 px-4 py-3 text-sm font-medium text-red-500">
          {refusing.length === 1
            ? "One ceiling is refusing requests right now \u2014 the top row."
            : `${refusing.length} ceilings are refusing requests right now \u2014 the top rows.`}
        </p>
      )}

      {/* The whole platform, which no per-client row can add up to: requests
          and tokens are counted once per request at a reserved identity, so
          summing the consumers would double-count every call that carries a
          distinct user. */}
      <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="rounded-xl border border-border bg-surface p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
            Platform, this minute
          </p>
          <div className="mt-3 space-y-3">
            <div>
              <p className="text-[11px] text-text-muted">requests / minute</p>
              <Meter row={platformRpm} />
            </div>
            <div>
              <p className="text-[11px] text-text-muted">tokens / minute</p>
              <Meter row={platformTpm} />
            </div>
          </div>
          {!platformRpm && !platformTpm && (
            <p className="mt-3 text-xs text-text-muted">
              Nothing has passed through this minute.
            </p>
          )}
        </div>

        <div className="rounded-xl border border-border bg-surface p-4 sm:col-span-2">
          <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
            Closest to a ceiling
          </p>
          {clients.length === 0 || !clients[0].worst ? (
            <p className="mt-3 text-sm text-text-muted">{quiet}</p>
          ) : (
            <>
              <p className="mt-2 text-sm text-text">
                <span className={toneOf(clients[0].worst.percent).text}>
                  {clients[0].worst.percent}%
                </span>{" "}
                — {names.get(clients[0].identity) ?? clients[0].identity} on{" "}
                <span className="font-mono text-xs">
                  {clients[0].worst.dimension}
                </span>{" "}
                {clients[0].worst.endpoint === "*"
                  ? "across every endpoint"
                  : `at ${clients[0].worst.endpoint}`}
              </p>
              <p className="mt-1 text-xs text-text-muted">
                {/* Stated because a percentage of a daily budget and a
                    percentage of a minute's are not the same warning. */}
                {clients[0].worst.window === "day"
                  ? "A daily budget — it does not reset until the next UTC midnight."
                  : "A per-minute budget — it resets on its own within the minute."}
              </p>
            </>
          )}
        </div>
      </div>

      {clients.length > 0 && (
        <div className="mt-4 overflow-x-auto rounded-xl border border-border bg-surface">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                <th className="px-4 py-3 font-medium">Consumer</th>
                <th className="px-4 py-3 font-medium">Requests / min</th>
                <th className="px-4 py-3 font-medium">Tokens / min</th>
                <th className="px-4 py-3 font-medium">Closest ceiling</th>
              </tr>
            </thead>
            <tbody>
              {clients.map((c) => (
                <tr
                  key={c.identity}
                  className="border-b border-border last:border-0"
                >
                  <td className="px-4 py-3">
                    <p className="truncate text-text">
                      {names.get(c.identity) ?? c.identity}
                    </p>
                    {names.has(c.identity) && (
                      <p className="mt-0.5 truncate font-mono text-[11px] text-text-muted">
                        {c.identity}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <Meter row={c.rpm} />
                  </td>
                  <td className="px-4 py-3">
                    <Meter row={c.tpm} />
                  </td>
                  <td className="px-4 py-3">
                    {c.worst ? (
                      <>
                        <p
                          className={`text-sm tabular-nums ${toneOf(c.worst.percent).text}`}
                        >
                          {c.worst.percent}%
                        </p>
                        <p className="mt-0.5 text-[11px] text-text-muted">
                          <span className="font-mono">{c.worst.dimension}</span>
                          {c.worst.endpoint === "*"
                            ? " · all endpoints"
                            : ` · ${c.worst.endpoint}`}
                        </p>
                      </>
                    ) : (
                      <span
                        className="text-xs text-text-muted"
                        title="Every counter this consumer has standing is one nothing checks — so none of them can refuse a request."
                      >
                        nothing checked
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-muted">
        {rows.length > 0 && (
          <button
            type="button"
            onClick={() => setShowDetail((v) => !v)}
            className="underline decoration-dotted hover:text-text"
          >
            {showDetail
              ? "Hide the individual counters"
              : `Show all ${rows.length} counters`}
          </button>
        )}
        {live?.minute_resets_in !== undefined && (
          <span className="tabular-nums">
            minute bucket resets in {live.minute_resets_in}s
          </span>
        )}
      </div>

      {showDetail && (
        <div className="mt-2 overflow-x-auto rounded-xl border border-dashed border-border bg-surface">
          <p className="px-4 pt-3 text-xs text-text-muted">
            Every counter standing, closest to refusing first. A row reading{" "}
            <span className="text-text">counted, not checked</span> is
            incremented and read by no ceiling — it cannot refuse anything, and
            it is not headroom either. Setting the matching limit above turns a
            meter into a ceiling.
          </p>
          <table className="w-full min-w-[720px] text-left text-sm">
            <tbody>
              {rows.map((row) => (
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
          What the platform and each consumer are spending against the ceilings
          that apply to them, closest to refusing first. Counters are per-bucket
          and reset on their own, so this is the current minute&rsquo;s traffic
          — or today&rsquo;s, for the daily ones — not a trend. The
          dashboard&rsquo;s own requests are in here too, under the admin
          endpoint.
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
