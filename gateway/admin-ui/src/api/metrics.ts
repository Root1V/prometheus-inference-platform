import { useQuery } from "@tanstack/react-query";

import { rootClient } from "./client";

/** Per-backend (= per-model) counters and circuit-breaker state. */
export interface BackendMetrics {
  requests_total: number;
  /** "closed" (healthy) | "open" (tripped) | "half-open" (probing) — see circuit_breaker.py. */
  circuit_state?: "closed" | "open" | "half-open" | "unknown";
  /** RM-46: per-model performance. All null until a request that can report
   * them actually happens — ttft needs a streamed request (chat only, no
   * streaming exists for embeddings/images), inter_token needs a
   * llama.cpp-family backend's own `timings` object (chat only — neither
   * embeddings nor images responses carry one), tokens_per_second is output
   * tok/s for chat or input tok/s for embeddings (no completion tokens
   * exist there), images_per_second is the image-generation-only analog
   * (no token concept applies to an image response at all). */
  latency_p50_ms: number;
  latency_p95_ms: number;
  ttft_p50_ms: number | null;
  inter_token_ms_avg: number | null;
  tokens_per_second_avg: number | null;
  images_per_second_avg: number | null;
  /** RM-62: prefill (prompt-processing) throughput — separate from
   * tokens_per_second_avg's decode-phase rate for chat. Only populated for
   * llama.cpp-family backends, whose `timings` object reports it directly. */
  prompt_tokens_per_second_avg: number | null;
}

/**
 * Gateway's GET /metrics — process-in-memory operational counters (see
 * gateway/src/prometheus_gateway/telemetry.py). Counters are cumulative since the
 * gateway process started — a restart zeroes them, and there's no historical
 * trend, only the current snapshot.
 *
 * Requires `admin:read` as of PRM-163. It was unauthenticated, which put
 * per-instance circuit state and `jwt_validations_failed` behind no credential at
 * all while `/v1/backends` gated the same figures.
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
  /** RM-79: whether the things every request needs are reachable. Redis
   * being down fails every authenticated request closed, and nothing else
   * says so — /health stays green because the process is alive. */
  dependencies?: {
    redis: { configured: boolean; reachable: boolean | null; impact?: string };
  };
}

const METRICS_KEY = ["gateway-metrics"] as const;
const POLL_INTERVAL_MS = 5000;

export function useMetrics() {
  return useQuery({
    queryKey: METRICS_KEY,
    // Root-level path, not under /admin/api, so `rootClient` rather than
    // `apiClient` — but through a client all the same: PRM-163 put `/metrics`
    // behind `admin:read`, and the session's token is attached by rootClient's
    // interceptor. A plain `axios.get` here would now 401.
    queryFn: async () => (await rootClient.get<MetricsSnapshot>("/metrics")).data,
    refetchInterval: POLL_INTERVAL_MS,
  });
}
