import { useQuery } from "@tanstack/react-query";
import { rootClient } from "./client";

export interface ModelUsageEntry {
  model_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
  /** null when this model has no configured price (docs/roadmap.md RM-33) — never $0. */
  estimated_cost_usd: number | null;
}

export interface UsageEntry {
  client_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
  estimated_cost_usd: number | null;
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

/**
 * RM-60: GET /v1/usage/export as a client-side file download. A plain
 * `<a href>` navigation would 401 — the Bearer token lives in sessionStorage
 * and is only attached by rootClient's axios interceptor — so this fetches
 * through rootClient and triggers the download via a Blob + synthetic click.
 */
export async function downloadUsageExportCsv(params: {
  start: string;
  end: string;
  client_id?: string;
}): Promise<void> {
  const response = await rootClient.get<string>("/v1/usage/export", {
    params,
    responseType: "text",
  });
  const blob = new Blob([response.data], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `usage-${params.start}-to-${params.end}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
