import axios from "axios";
import { useQuery } from "@tanstack/react-query";

/**
 * Gateway's GET /metrics — unauthenticated, process-in-memory operational
 * counters (see gateway/src/prometheus_gateway/telemetry.py). Only the
 * fields the Overview page's at-a-glance strip needs are typed here; RM-28
 * widens this when it builds the golden-signals row from the rest of the
 * snapshot.
 */
export interface MetricsSnapshot {
  service: string;
  uptime_seconds: number;
}

const METRICS_KEY = ["gateway-metrics"] as const;
const POLL_INTERVAL_MS = 5000;

export function useMetrics() {
  return useQuery({
    queryKey: METRICS_KEY,
    // Root-level path, not under /admin/api — /metrics is unauthenticated
    // and same-origin (the SPA is served by the gateway itself).
    queryFn: async () => (await axios.get<MetricsSnapshot>("/metrics")).data,
    refetchInterval: POLL_INTERVAL_MS,
  });
}
