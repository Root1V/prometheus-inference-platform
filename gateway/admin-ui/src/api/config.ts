import { useQuery } from "@tanstack/react-query";
import { apiClient } from "./client";

/** GET /admin/api/config — dashboard-facing settings (docs/roadmap.md RM-31). */
export interface DashboardConfig {
  /** null when Grafana isn't deployed — the Overview page omits the link entirely. */
  grafana_url: string | null;
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
