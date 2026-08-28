import { useQuery } from "@tanstack/react-query";
import { rootClient } from "./client";

export interface UsageEntry {
  client_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
}

/** Gateway's GET /v1/usage — today's (UTC) per-client token totals only, no history. */
export interface UsageResponse {
  object: string;
  window: string;
  data: UsageEntry[];
}

const USAGE_KEY = ["gateway-usage"] as const;
const POLL_INTERVAL_MS = 15000;

export function useUsage() {
  return useQuery({
    queryKey: USAGE_KEY,
    // Root-level path, not under /admin/api — same-origin, requires admin:read.
    queryFn: async () => (await rootClient.get<UsageResponse>("/v1/usage")).data,
    refetchInterval: POLL_INTERVAL_MS,
  });
}
