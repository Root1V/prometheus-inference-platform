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

export interface OverviewResponse {
  days: number;
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
  fleet: { uptime_seconds: number | null; requests_active: number | null };
}

export function useOverview() {
  return useQuery({
    queryKey: ["overview"],
    queryFn: async () =>
      (await apiClient.get<OverviewResponse>("/overview")).data,
    refetchInterval: 15_000,
    retry: false,
  });
}
