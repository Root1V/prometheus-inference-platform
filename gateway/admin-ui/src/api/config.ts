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

const CONFIG_KEY = ["gateway-dashboard-config"] as const;

export function useDashboardConfig() {
  return useQuery({
    queryKey: CONFIG_KEY,
    queryFn: async () => (await apiClient.get<DashboardConfig>("/config")).data,
    // Static until the gateway restarts — no need to poll.
    staleTime: Infinity,
  });
}
