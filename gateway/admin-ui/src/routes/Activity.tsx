import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import {
  useActivity,
  type ActivityConsumer,
  type ActivityCounter,
  type EndUserToday,
  type ModelUse,
} from "../api/activity";
import { useUsers } from "../api/users";
import { Sidebar } from "../components/Sidebar";
import { getErrorMessage } from "../lib/errors";
import { formatAgo, formatUsdCost } from "../lib/format";

/**
 * Activity — PRM-235, reorganised by PRM-236.
 *
 * PRM-235 replaced a page that listed presence with one that listed
 * consumption. What it still could not answer was *what* a credential did:
 * one table of request counts says a client was busy and not whether it ran
 * inference, listed its models, or opened the Playground. The page is now
 * three sections, one per question an operator actually arrives with — who is
 * here now and what are they doing, which consumers used the platform today,
 * and which people were behind them.
 */

function toneOf(percent: number | null) {
  if (percent === null)
    return { bar: "bg-text-muted/30", text: "text-text-muted" };
  if (percent >= 100)
    return { bar: "bg-red-500", text: "font-medium text-red-500" };
  if (percent >= 80)
    return { bar: "bg-amber-500", text: "text-amber-600 dark:text-amber-400" };
  return { bar: "bg-primary", text: "text-text-muted" };
}

function Meter({ row }: { row: ActivityCounter | null }) {
  if (!row) return <span className="text-xs text-text-muted">—</span>;
  const tone = toneOf(row.percent);
  return (
    <div>
      <p className="whitespace-nowrap text-sm tabular-nums text-text">
        {row.used.toLocaleString()}
        <span className="text-text-muted">
          {" / "}
          {row.limit === null ? "—" : row.limit.toLocaleString()}
        </span>
      </p>
      <div className="mt-1 flex items-center gap-2">
        <div className="h-1 w-20 overflow-hidden rounded-full bg-background">
          {row.percent !== null && (
            <div
              className={`h-full rounded-full ${tone.bar}`}
              style={{ width: `${Math.min(100, row.percent)}%` }}
            />
          )}
        </div>
        <span
          className={`whitespace-nowrap text-[11px] tabular-nums ${tone.text}`}
        >
          {row.percent === null ? "no ceiling" : `${row.percent}%`}
        </span>
      </div>
    </div>
  );
}

/** One model, with the kind of call that reached it — PRM-236. An embedding
 *  and a chat completion against the same name are different work. */
function ModelChips({ models }: { models: ModelUse[] }) {
  if (models.length === 0)
    return <span className="text-xs text-text-muted">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {models.map((m) => (
        <span
          key={`${m.model}:${m.request_kind}`}
          className="rounded bg-background px-1.5 py-0.5 text-[11px] text-text-muted"
          title={`${m.request_count} ${m.request_kind} request${m.request_count === 1 ? "" : "s"}`}
        >
          <span className="font-mono text-text">{m.model ?? "unknown"}</span>
          <span className="ml-1 tabular-nums">×{m.request_count}</span>
        </span>
      ))}
    </span>
  );
}

/** What a credential has been doing, from the in-process tracker — PRM-236. */
function ActionChips({ consumer }: { consumer: ActivityConsumer }) {
  if (consumer.actions.length === 0) {
    return (
      <span
        className="text-xs text-text-muted"
        title="The gateway tracks this in memory, so it knows nothing from before its last restart. Today's totals come from the database and are unaffected."
      >
        not tracked since the gateway started
      </span>
    );
  }
  return (
    <span className="flex flex-wrap gap-1">
      {consumer.actions.map((a) => (
        <span
          key={a.action}
          className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] text-primary"
          title={`${a.count} request${a.count === 1 ? "" : "s"}, last ${formatAgo(a.last_seen_ago_s)}`}
        >
          {a.label}
          <span className="ml-1 tabular-nums opacity-70">×{a.count}</span>
        </span>
      ))}
    </span>
  );
}

function EndUserRows({ users }: { users: EndUserToday[] }) {
  return (
    <table className="mt-2 w-full text-left text-sm">
      <thead>
        <tr className="text-[11px] uppercase tracking-wide text-text-muted">
          <th className="py-1 pr-4 font-medium">End user</th>
          <th className="py-1 pr-4 font-medium">Requests</th>
          <th className="py-1 pr-4 font-medium">Tokens</th>
          <th className="py-1 pr-4 font-medium">Cost</th>
          <th className="py-1 font-medium">Models</th>
        </tr>
      </thead>
      <tbody>
        {users.map((u) => (
          <tr
            key={u.end_user ?? "__none__"}
            className="border-t border-border/60 align-top"
          >
            <td className="py-1.5 pr-4">
              {u.end_user === null ? (
                <span
                  className="text-xs italic text-text-muted"
                  title="Requests that carried no end-user field. Not an error — a caller that identifies nobody is identifying nobody, and this is how much of its traffic that is."
                >
                  no end user named
                </span>
              ) : (
                <span className="font-mono text-xs text-text">
                  {u.end_user}
                </span>
              )}
            </td>
            <td className="py-1.5 pr-4 tabular-nums text-text-muted">
              {u.request_count.toLocaleString()}
            </td>
            <td className="py-1.5 pr-4 tabular-nums text-text-muted">
              {u.total_tokens.toLocaleString()}
            </td>
            <td className="py-1.5 pr-4 tabular-nums text-text-muted">
              {formatUsdCost(u.cost_usd)}
            </td>
            <td className="py-1.5">
              <ModelChips models={u.models} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ConsumerRow({
  consumer,
  name,
  showLive,
}: {
  consumer: ActivityConsumer;
  name: string;
  showLive: boolean;
}) {
  const [open, setOpen] = useState(false);
  const expandable = consumer.end_users.length > 0;

  return (
    <>
      <tr className="border-b border-border last:border-0 align-top">
        <td className="px-4 py-3">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            disabled={!expandable}
            className="flex items-start gap-1.5 text-left disabled:cursor-default"
          >
            <span className="mt-0.5 text-text-muted">
              {!expandable ? (
                <span className="inline-block w-3.5" />
              ) : open ? (
                <ChevronDown size={14} />
              ) : (
                <ChevronRight size={14} />
              )}
            </span>
            <span className="min-w-0">
              <span className="block truncate text-text">{name}</span>
              <span className="mt-0.5 block truncate font-mono text-[11px] text-text-muted">
                {consumer.identity}
              </span>
            </span>
          </button>
        </td>
        {showLive ? (
          <>
            <td className="whitespace-nowrap px-4 py-3">
              <p className="text-sm text-text">
                {consumer.last_seen_ago_s === null
                  ? "—"
                  : formatAgo(consumer.last_seen_ago_s)}
              </p>
            </td>
            <td className="px-4 py-3">
              <ActionChips consumer={consumer} />
            </td>
            <td className="px-4 py-3">
              <Meter row={consumer.rpm} />
            </td>
            <td className="px-4 py-3">
              <Meter row={consumer.tpm} />
            </td>
            <td className="px-4 py-3">
              {consumer.worst ? (
                <>
                  <p
                    className={`text-sm tabular-nums ${toneOf(consumer.worst.percent).text}`}
                  >
                    {consumer.worst.percent}%
                  </p>
                  <p className="mt-0.5 whitespace-nowrap text-[11px] text-text-muted">
                    <span className="font-mono">
                      {consumer.worst.dimension}
                    </span>
                    {consumer.worst.endpoint === "*"
                      ? " · all endpoints"
                      : ` · ${consumer.worst.endpoint}`}
                  </p>
                </>
              ) : (
                <span className="text-xs text-text-muted">—</span>
              )}
            </td>
          </>
        ) : (
          <>
            <td className="px-4 py-3">
              <ModelChips models={consumer.models} />
            </td>
            <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-text">
              {consumer.today.request_count.toLocaleString()}
            </td>
            <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-text-muted">
              {consumer.today.total_tokens.toLocaleString()}
            </td>
            <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-text-muted">
              {formatUsdCost(consumer.today.cost_usd)}
            </td>
          </>
        )}
      </tr>
      {open && (
        <tr className="border-b border-border bg-background/40 last:border-0">
          <td colSpan={showLive ? 6 : 5} className="px-4 py-3">
            <p className="text-xs text-text-muted">
              Who this consumer served today, from the <code>user</code> /{" "}
              <code>safety_identifier</code> field or the{" "}
              <code>x-prometheus-end-user</code> header.
            </p>
            <EndUserRows users={consumer.end_users} />
          </td>
        </tr>
      )}
    </>
  );
}

export default function Activity() {
  const activityQuery = useActivity();
  const usersQuery = useUsers();

  const names = new Map(
    (usersQuery.data ?? []).map((u) => [
      u.client_id,
      u.label || u.client_name || u.client_id,
    ]),
  );
  const nameOf = (id: string) => names.get(id) ?? id;

  const data = activityQuery.data;
  const consumers = data?.consumers ?? [];
  const windowMinutes = data?.tracker_window_minutes ?? 15;
  // "Here now" is a measurement with a definition, so the page states it: a
  // request inside the tracker's window. Everything else is today's history,
  // which outlives both the window and the process.
  const here = consumers.filter((c) => c.last_seen_ago_s !== null);
  const today = consumers.filter((c) => c.today.request_count > 0);
  const endUsers = data?.end_users_today ?? [];

  const empty = (text: string) => (
    <div className="p-10 text-center text-sm text-text-muted">{text}</div>
  );

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="min-w-0 flex-1 px-8 py-8">
        <h1 className="text-2xl font-semibold text-text">Activity</h1>
        <p className="mt-1 max-w-4xl text-sm text-text-muted">
          Who is calling this platform, what they are doing, and who they are
          doing it for. Live figures come from the rate-limit counters and the
          gateway&rsquo;s own memory; today&rsquo;s come from the usage rows,
          which is why a restart changes the first two sections and not the
          third.
        </p>

        {activityQuery.isError && (
          <p className="mt-4 rounded-xl border border-border bg-surface px-4 py-3 text-sm text-red-600">
            {getErrorMessage(activityQuery.error)}
          </p>
        )}

        {/* ── Here now ───────────────────────────────────────────────────── */}
        <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-text-muted">
          Here now
        </h2>
        <p className="mt-1 max-w-4xl text-sm text-text-muted">
          Credentials that have made a request in the last {windowMinutes}{" "}
          minutes, what kind of request it was, and what they are spending this
          minute. There is no connection to list &mdash; tokens are stateless,
          so &ldquo;here&rdquo; means &ldquo;called recently&rdquo;.
        </p>
        <div className="mt-3 overflow-x-auto rounded-xl border border-border bg-surface">
          {activityQuery.isLoading ? (
            empty("Reading activity…")
          ) : here.length === 0 ? (
            empty(`Nobody has called in the last ${windowMinutes} minutes.`)
          ) : (
            <table className="w-full min-w-[1040px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                  <th className="px-4 py-3 font-medium">Consumer</th>
                  <th className="px-4 py-3 font-medium">Last call</th>
                  <th className="px-4 py-3 font-medium">What they are doing</th>
                  <th className="px-4 py-3 font-medium">Requests / min</th>
                  <th className="px-4 py-3 font-medium">Tokens / min</th>
                  <th className="px-4 py-3 font-medium">Closest ceiling</th>
                </tr>
              </thead>
              <tbody>
                {here.map((consumer) => (
                  <ConsumerRow
                    key={consumer.identity}
                    consumer={consumer}
                    name={nameOf(consumer.identity)}
                    showLive
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* ── Today, by consumer ─────────────────────────────────────────── */}
        <h2 className="mt-10 text-sm font-medium uppercase tracking-wide text-text-muted">
          Consumers today
        </h2>
        <p className="mt-1 max-w-4xl text-sm text-text-muted">
          Every credential that ran something today and what it ran. From the
          usage rows, so a gateway restart does not change this &mdash; and a
          consumer here with nothing in &ldquo;Here now&rdquo; simply has not
          called in the last {windowMinutes} minutes. Expand a row for the
          people behind it.
        </p>
        <div className="mt-3 overflow-x-auto rounded-xl border border-border bg-surface">
          {activityQuery.isLoading ? (
            empty("Reading activity…")
          ) : today.length === 0 ? (
            empty("Nothing has been billed today yet.")
          ) : (
            <table className="w-full min-w-[840px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                  <th className="px-4 py-3 font-medium">Consumer</th>
                  <th className="px-4 py-3 font-medium">Models used</th>
                  <th className="px-4 py-3 text-right font-medium">Requests</th>
                  <th className="px-4 py-3 text-right font-medium">Tokens</th>
                  <th className="px-4 py-3 text-right font-medium">Cost</th>
                </tr>
              </thead>
              <tbody>
                {today.map((consumer) => (
                  <ConsumerRow
                    key={consumer.identity}
                    consumer={consumer}
                    name={nameOf(consumer.identity)}
                    showLive={false}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* ── Today, by end user ─────────────────────────────────────────── */}
        <h2 className="mt-10 text-sm font-medium uppercase tracking-wide text-text-muted">
          End users today
        </h2>
        <p className="mt-1 max-w-4xl text-sm text-text-muted">
          The same day from the other side. One person can be served by several
          consumers, which a list nested inside each one cannot show. Only
          requests that carried <code>user</code>,{" "}
          <code>safety_identifier</code> or the{" "}
          <code>x-prometheus-end-user</code> header appear here.
        </p>
        <div className="mt-3 overflow-x-auto rounded-xl border border-border bg-surface">
          {endUsers.length === 0 ? (
            empty(
              "No caller has named an end user today — their traffic is still counted, under the consumer that sent it.",
            )
          ) : (
            <table className="w-full min-w-[840px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                  <th className="px-4 py-3 font-medium">End user</th>
                  <th className="px-4 py-3 font-medium">Through</th>
                  <th className="px-4 py-3 font-medium">Models</th>
                  <th className="px-4 py-3 text-right font-medium">Requests</th>
                  <th className="px-4 py-3 text-right font-medium">Tokens</th>
                  <th className="px-4 py-3 text-right font-medium">Cost</th>
                </tr>
              </thead>
              <tbody>
                {endUsers.map((u) => (
                  <tr
                    key={u.end_user}
                    className="border-b border-border last:border-0"
                  >
                    <td className="px-4 py-3 font-mono text-xs text-text">
                      {u.end_user}
                    </td>
                    <td className="px-4 py-3 text-xs text-text-muted">
                      {u.consumers.map((id) => nameOf(id)).join(", ")}
                    </td>
                    <td className="px-4 py-3">
                      <span className="flex flex-wrap gap-1">
                        {u.models.map((m) => (
                          <span
                            key={m}
                            className="rounded bg-background px-1.5 py-0.5 font-mono text-[11px] text-text"
                          >
                            {m}
                          </span>
                        ))}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-text">
                      {u.request_count.toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-text-muted">
                      {u.total_tokens.toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-text-muted">
                      {formatUsdCost(u.cost_usd)}
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
