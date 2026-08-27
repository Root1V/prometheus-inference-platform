import axios from "axios";
import { useQuery } from "@tanstack/react-query";

/** Per-backend (= per-model) counters and circuit-breaker state. */
export interface BackendMetrics {
  requests_total: number;
  /** "closed" (healthy) | "open" (tripped) | "half-open" (probing) — see circuit_breaker.py. */
  circuit_state?: "closed" | "open" | "half-open" | "unknown";
}

/**
 * Gateway's GET /metrics — unauthenticated, process-in-memory operational
 * counters (see gateway/src/prometheus_gateway/telemetry.py). Counters are
 * cumulative since the gateway process started — a restart zeroes them, and
 * there's no historical trend, only the current snapshot.
 */
export interface MetricsSnapshot {
  service: string;
  uptime_seconds: number;
  inference: {
    requests_total: number;
    requests_active: number;
    tokens_prompt_total: number;
    tokens_completion_total: number;
    errors_total: number;
    latency_p50_ms: number;
    latency_p95_ms: number;
    latency_p99_ms: number;
  };
  backends: Record<string, BackendMetrics>;
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
