import { useQuery } from "@tanstack/react-query";
import { apiClient } from "./client";

/** GET /admin/api/config — dashboard-facing settings (docs/roadmap.md RM-31, RM-16). */
export interface DashboardConfig {
  /** null when Grafana isn't deployed — the Overview page omits the link entirely. */
  grafana_url: string | null;
  rate_limit_rpm: number;
  rate_limit_tpm: number;
  /** null when no per-endpoint override is configured for /v1/chat/completions. */
  rate_limit_rpm_chat_completions: number | null;
  rate_limit_tpm_chat_completions: number | null;
  /** true = deny when the rate-limit store is unreachable; false = fail-open. */
  rate_limit_strict: boolean;
  circuit_breaker_failure_threshold: number;
  circuit_breaker_recovery_timeout: number;
  circuit_breaker_success_threshold: number;
}

export const CONFIG_KEY = ["gateway-dashboard-config"] as const;

export function useDashboardConfig() {
  return useQuery({
    queryKey: CONFIG_KEY,
    queryFn: async () => (await apiClient.get<DashboardConfig>("/config")).data,
    // Nothing here changes on its own, so polling would be waste — but the
    // rate-limit values are editable since RM-56, so api/limits.ts invalidates
    // this key on save rather than leaving a stale snapshot on screen.
    staleTime: Infinity,
  });
}
