import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import {
  useActivity,
  type ActivityConsumer,
  type ActivityCounter,
} from "../api/activity";
import { useUsers } from "../api/users";
import { Sidebar } from "../components/Sidebar";
import { getErrorMessage } from "../lib/errors";
import { formatAgo, formatUsdCost } from "../lib/format";

/**
 * Activity — PRM-235, replacing Sessions.
 *
 * The page it replaces listed credentials seen in the last fifteen minutes and
 * nothing else: three columns, from in-process memory that a restart empties.
 * It was also the wrong word. In LLM tooling a *session* is the grouping of one
 * conversation's or one agent run's calls — Langfuse groups traces by a session
 * id, Helicone groups requests into a tree — while what gateways actually
 * publish is traffic per consumer: Kong labels its request counters by Consumer
 * and meters each call against one, LiteLLM breaks spend and requests down per
 * key and per customer. This page is the second thing, under the second name.
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

const CONNECTION_LABEL: Record<string, string> = {
  dashboard: "Dashboard",
  api: "API / SDK",
  other: "Other",
};

function ConsumerRow({
  consumer,
  name,
  windowMinutes,
}: {
  consumer: ActivityConsumer;
  name: string;
  windowMinutes: number;
}) {
  const [open, setOpen] = useState(false);
  // The unnamed row is not an end user, so it does not count as one — a
  // consumer whose only row is `null` has named nobody, and saying "1 end user"
  // there would be the page inventing a person.
  const named = consumer.end_users.filter((u) => u.end_user !== null);
  const unnamed = consumer.end_users.find((u) => u.end_user === null);

  return (
    <>
      <tr className="border-b border-border last:border-0">
        <td className="px-4 py-3">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            disabled={consumer.end_users.length === 0}
            className="flex items-start gap-1.5 text-left disabled:cursor-default"
          >
            <span className="mt-0.5 text-text-muted">
              {consumer.end_users.length === 0 ? (
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
        <td className="whitespace-nowrap px-4 py-3 text-text-muted">
          {consumer.last_seen_ago_s === null ? (
            <span
              className="text-xs"
              title={`Nothing in the last ${windowMinutes} minutes — or the gateway restarted since, which empties that tracker while the counters and the usage rows keep theirs.`}
            >
              not in the window
            </span>
          ) : (
            <>
              <p className="text-sm text-text">
                {formatAgo(consumer.last_seen_ago_s)}
              </p>
              {consumer.connection_type && (
                <p className="mt-0.5 text-[11px]">
                  {CONNECTION_LABEL[consumer.connection_type] ??
                    consumer.connection_type}
                </p>
              )}
            </>
          )}
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
                <span className="font-mono">{consumer.worst.dimension}</span>
                {consumer.worst.endpoint === "*"
                  ? " · all endpoints"
                  : ` · ${consumer.worst.endpoint}`}
              </p>
            </>
          ) : (
            <span className="text-xs text-text-muted">—</span>
          )}
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-right">
          <p className="text-sm tabular-nums text-text">
            {consumer.today.request_count.toLocaleString()}
          </p>
          <p className="mt-0.5 text-[11px] tabular-nums text-text-muted">
            {consumer.today.total_tokens.toLocaleString()} tok ·{" "}
            {formatUsdCost(consumer.today.cost_usd)}
          </p>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-border bg-background/40 last:border-0">
          <td colSpan={6} className="px-4 py-3">
            <p className="text-xs text-text-muted">
              End users this consumer named today, from the <code>user</code> /{" "}
              <code>safety_identifier</code> field or the{" "}
              <code>x-prometheus-end-user</code> header.
            </p>
            <table className="mt-2 w-full text-left text-sm">
              <tbody>
                {named.map((u) => (
                  <tr key={u.end_user} className="border-t border-border/60">
                    <td className="py-1.5 pr-4 font-mono text-xs text-text">
                      {u.end_user}
                    </td>
                    <td className="py-1.5 pr-4 tabular-nums text-text-muted">
                      {u.request_count.toLocaleString()} req
                    </td>
                    <td className="py-1.5 pr-4 tabular-nums text-text-muted">
                      {u.total_tokens.toLocaleString()} tok
                    </td>
                    <td className="py-1.5 tabular-nums text-text-muted">
                      {formatUsdCost(u.cost_usd)}
                    </td>
                  </tr>
                ))}
                {unnamed && (
                  <tr className="border-t border-border/60">
                    <td
                      className="py-1.5 pr-4 text-xs italic text-text-muted"
                      title="Requests that carried no end-user field. Not an error — a caller that identifies nobody is identifying nobody, and this is how much of its traffic that is."
                    >
                      no end user named
                    </td>
                    <td className="py-1.5 pr-4 tabular-nums text-text-muted">
                      {unnamed.request_count.toLocaleString()} req
                    </td>
                    <td className="py-1.5 pr-4 tabular-nums text-text-muted">
                      {unnamed.total_tokens.toLocaleString()} tok
                    </td>
                    <td className="py-1.5 tabular-nums text-text-muted">
                      {formatUsdCost(unnamed.cost_usd)}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
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
  const data = activityQuery.data;
  const consumers = data?.consumers ?? [];
  const platform = data?.platform;

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="min-w-0 flex-1 px-8 py-8">
        <h1 className="text-2xl font-semibold text-text">Activity</h1>
        <p className="mt-1 max-w-4xl text-sm text-text-muted">
          Who is calling this platform and how much. The minute figures come
          from the rate-limit counters, today&rsquo;s from the usage rows, and
          last-seen from the gateway&rsquo;s own memory &mdash; which a restart
          empties, while the other two survive it. Tokens are stateless, so
          there is no connection to list: this is traffic, not presence.
        </p>

        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-border bg-surface p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Platform, this minute
            </p>
            <div className="mt-3 space-y-3">
              <div>
                <p className="text-[11px] text-text-muted">requests / minute</p>
                <Meter row={platform?.rpm ?? null} />
              </div>
              <div>
                <p className="text-[11px] text-text-muted">tokens / minute</p>
                <Meter row={platform?.tpm ?? null} />
              </div>
            </div>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Consumers today
            </p>
            <p className="mt-3 text-3xl font-semibold tabular-nums text-text">
              {consumers.filter((c) => c.today.request_count > 0).length}
            </p>
            <p className="mt-1 text-xs text-text-muted">
              {consumers.length} with activity of any kind
              {data?.omitted ? `, ${data.omitted} more not listed` : ""}
            </p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
              End users named today
            </p>
            <p className="mt-3 text-3xl font-semibold tabular-nums text-text">
              {
                new Set(
                  consumers.flatMap((c) =>
                    c.end_users
                      .filter((u) => u.end_user !== null)
                      .map((u) => u.end_user),
                  ),
                ).size
              }
            </p>
            <p className="mt-1 text-xs text-text-muted">
              {/* Zero is the expected reading until callers start sending it,
                  and saying so beats a zero that looks like a broken query. */}
              across every consumer — zero until callers send <code>user</code>{" "}
              or <code>safety_identifier</code>
            </p>
          </div>
        </div>

        <div className="mt-6 overflow-x-auto rounded-xl border border-border bg-surface">
          {activityQuery.isLoading ? (
            <div className="p-12 text-center text-text-muted">
              Reading activity…
            </div>
          ) : activityQuery.isError ? (
            <div className="p-12 text-center text-red-600">
              {getErrorMessage(activityQuery.error)}
            </div>
          ) : consumers.length === 0 ? (
            <div className="p-12 text-center text-text-muted">
              No traffic today and nothing in the last{" "}
              {data?.tracker_window_minutes ?? 15} minutes.
            </div>
          ) : (
            <table className="w-full min-w-[920px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                  <th className="px-4 py-3 font-medium">Consumer</th>
                  <th className="px-4 py-3 font-medium">Last seen</th>
                  <th className="px-4 py-3 font-medium">Requests / min</th>
                  <th className="px-4 py-3 font-medium">Tokens / min</th>
                  <th className="px-4 py-3 font-medium">Closest ceiling</th>
                  <th className="px-4 py-3 text-right font-medium">Today</th>
                </tr>
              </thead>
              <tbody>
                {consumers.map((consumer) => (
                  <ConsumerRow
                    key={consumer.identity}
                    consumer={consumer}
                    name={names.get(consumer.identity) ?? consumer.identity}
                    windowMinutes={data?.tracker_window_minutes ?? 15}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </div>
  );
}
