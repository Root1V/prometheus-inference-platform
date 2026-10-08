import { useQuery } from "@tanstack/react-query";
import { apiClient } from "./client";

/** One live rate-limit counter, as the Activity page needs it — PRM-235.
 *  Same shape the Limits page reads; see `useLiveLimits`. */
export interface ActivityCounter {
  dimension: string;
  endpoint: string;
  window: "minute" | "day";
  used: number;
  layer: "platform" | "client" | "endpoint";
  limit: number | null;
  limit_source: "tier" | "set" | "derived" | "none";
  percent: number | null;
}

/** One end user a consumer named today. `end_user: null` is the traffic the
 *  caller identified nobody for — usually most of it, and a real answer
 *  rather than a gap. */
/** What a consumer ran today, per model and request kind — PRM-236. */
export interface ModelUse {
  model: string | null;
  request_kind: string;
  request_count: number;
  total_tokens?: number;
  cost_usd?: number | null;
}

export interface EndUserToday {
  end_user: string | null;
  request_count: number;
  total_tokens: number;
  cost_usd: number | null;
  last_seen: string | null;
  /** Which models this end user's requests went to. */
  models: ModelUse[];
}

/** One kind of thing a credential did, counted by the in-process tracker —
 *  PRM-236. Inference, listing its models, the dashboard, the Playground. */
export interface ConsumerAction {
  action: string;
  label: string;
  /** "usage" when the tracker had nothing and the row was reconstructed from
   *  the usage rows — which only know about calls that bill. */
  source?: "usage";
  /** Requests of this kind **inside the window**, not since the gateway
   *  started — PRM-237 made the number and its heading agree. */
  count: number;
  last_seen_ago_s: number;
}

/** One end user across every consumer that served them today. */
export interface EndUserAcrossConsumers {
  end_user: string;
  request_count: number;
  total_tokens: number;
  cost_usd: number;
  consumers: string[];
  models: string[];
}

export interface ActivityConsumer {
  identity: string;
  /** PRM-237: this row is the credential reading the page. The dashboard
   *  polls, so an operator who only opened Activity is on it. */
  is_you: boolean;
  /** Null when the in-process tracker has no entry — including after a gateway
   *  restart, which empties it while Redis and the database keep theirs. */
  last_seen_ago_s: number | null;
  /** Where last-seen came from: the in-process tracker, or the usage rows
   *  after a restart emptied it. */
  last_seen_source?: "tracker" | "usage" | null;
  connection_type: "dashboard" | "api" | "other" | null;
  rpm: ActivityCounter | null;
  tpm: ActivityCounter | null;
  /** The counter closest to its ceiling, across every layer and dimension. */
  worst: ActivityCounter | null;
  today: { request_count: number; total_tokens: number; cost_usd: number };
  end_users: EndUserToday[];
  /** What it did, from the tracker. Empty when the tracker has no entry —
   *  which is not the same as having done nothing. */
  actions: ConsumerAction[];
  /** What it ran today, per model. */
  models: ModelUse[];
  /** PRM-238: the same three figures for the live window, so the detail under
   *  a live row adds up to the pills on it instead of to the day. */
  window: { request_count: number; total_tokens: number; cost_usd: number };
  /** PRM-238: inference calls that *arrived* in the window, against the
   *  `window.request_count` that produced a usage row. Null when the actions
   *  were reconstructed from those same rows, where the two are equal by
   *  construction. */
  window_arrived: number | null;
  window_end_users: EndUserToday[];
  window_models: ModelUse[];
}

export interface ActivityResponse {
  consumers: ActivityConsumer[];
  /** The same day from the other side: one person can appear under several
   *  consumers, which a list nested inside each one cannot show. */
  end_users_today: EndUserAcrossConsumers[];
  /** Consumers beyond the top N — counted, not listed. */
  omitted: number;
  tracker_window_minutes: number;
  /** Seconds this gateway process has been up. Shorter than the window means
   *  the tracker's half of it covers less than the table says. */
  uptime_s: number;
  /** False while the gateway has been up for less than the window: the detail
   *  still covers all of it, but "arrived" would count a shorter span than
   *  "billed", so the page withholds the subtraction rather than printing one
   *  that reads backwards. */
  window_is_comparable: boolean;
  /** When the live window opens — one boundary for the action counters and
   *  the usage rows alike. */
  window_started_at: string;
  platform: { rpm: ActivityCounter | null; tpm: ActivityCounter | null };
}

export function useActivity() {
  return useQuery({
    queryKey: ["activity"],
    queryFn: async () =>
      (await apiClient.get<ActivityResponse>("/activity")).data,
    // The minute counters are the fastest-moving part of this page.
    refetchInterval: 10_000,
    retry: false,
  });
}
