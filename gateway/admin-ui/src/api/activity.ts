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
  /** Null when the in-process tracker has no entry — including after a gateway
   *  restart, which empties it while Redis and the database keep theirs. */
  last_seen_ago_s: number | null;
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
}

export interface ActivityResponse {
  consumers: ActivityConsumer[];
  /** The same day from the other side: one person can appear under several
   *  consumers, which a list nested inside each one cannot show. */
  end_users_today: EndUserAcrossConsumers[];
  /** Consumers beyond the top N — counted, not listed. */
  omitted: number;
  tracker_window_minutes: number;
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
