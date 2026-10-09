import { useQuery } from "@tanstack/react-query";
import { apiClient } from "./client";

/** One thing an operator should look at, ranked — PRM-246. */
export interface AttentionItem {
  kind: "ceiling" | "budget" | "circuit" | "unpriced";
  severity: "critical" | "warning";
  what: string;
  /** Where to go and do something about it. */
  where: string;
}

/** Today against yesterday. `percent` is null when there is nothing to
 *  compare against — a first day of traffic is not a 100% rise. */
export interface Delta {
  today: number;
  yesterday: number;
  percent: number | null;
}

export interface OverviewDay {
  day: string;
  request_count: number;
  tokens: number;
  cost_usd: number;
  interrupted: number;
  upstream_errors: number;
}

export interface ModelLatency {
  model: string;
  count: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
}

export interface OverviewModel {
  model_id: string;
  cost_usd: number | null;
  total_tokens: number;
  request_count: number;
  latency?: ModelLatency | null;
}

export interface OverviewClient {
  client_id: string;
  cost_usd: number | null;
  total_tokens: number;
  request_count: number;
  request_count_yesterday: number;
  percent: number | null;
}

/** PRM-247: a consumer's closest ceiling right now. Below the threshold that
 *  earns a place in `attention`, because "nobody is near a limit" and "three
 *  consumers sit at 40%" are different things to know before raising a tier. */
export interface Pressure {
  identity: string;
  percent: number;
  dimension: string;
  endpoint: string;
  used: number;
  limit: number;
}

/** One administrative change, from the audit table. */
export interface Change {
  at: string | null;
  actor: string | null;
  action: string;
  target: string | null;
  outcome: string;
}

export interface OverviewResponse {
  days: number;
  /** The windows the page may ask for. */
  windows: number[];
  attention: AttentionItem[];
  signals: {
    requests: Delta;
    tokens: Delta;
    cost_usd: Delta;
    interrupted: Delta;
    upstream_errors: Delta;
  };
  series: OverviewDay[];
  models: OverviewModel[];
  clients: OverviewClient[];
  pressure: Pressure[];
  changes: Change[];
  /** Who is reading the page, from the token. The name is resolved from the
   *  principal list — `Claims` carries no email. */
  you: { client_id: string | null; user_id: string | null };
  fleet: { uptime_seconds: number | null; requests_active: number | null };
}

export function useOverview(days: number) {
  return useQuery({
    // The window is part of the key, so switching to 30 days and back does
    // not re-fetch what is already cached.
    queryKey: ["overview", days],
    queryFn: async () =>
      (await apiClient.get<OverviewResponse>("/overview", { params: { days } }))
        .data,
    refetchInterval: 15_000,
    retry: false,
  });
}
