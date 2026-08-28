import { useQuery } from "@tanstack/react-query";
import { rootClient } from "./client";

export interface ModelUsageEntry {
  model_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
}

export interface UsageEntry {
  client_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
  by_model: ModelUsageEntry[];
}

/** Gateway's GET /v1/usage — per-client token totals (+ per-model breakdown) for one UTC day. */
export interface UsageResponse {
  object: string;
  window: string;
  data: UsageEntry[];
}

const USAGE_KEY = "gateway-usage";
const POLL_INTERVAL_MS = 15000;

/** @param date Optional YYYY-MM-DD (UTC) day to query; defaults to today server-side. */
export function useUsage(date?: string) {
  return useQuery({
    queryKey: [USAGE_KEY, date ?? "today"] as const,
    // Root-level path, not under /admin/api — same-origin, requires admin:read.
    queryFn: async () =>
      (
        await rootClient.get<UsageResponse>("/v1/usage", {
          params: date ? { date } : undefined,
        })
      ).data,
    // Only poll the live "today" view — a past date is immutable, no need to refetch it.
    refetchInterval: date ? false : POLL_INTERVAL_MS,
  });
}
