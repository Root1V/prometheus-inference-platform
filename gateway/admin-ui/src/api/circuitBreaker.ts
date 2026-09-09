import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import { CONFIG_KEY } from "./config";

export interface CircuitBreakerValues {
  circuit_breaker_failure_threshold: number;
  circuit_breaker_recovery_timeout: number;
  circuit_breaker_success_threshold: number;
}

/** GET/PUT/DELETE /admin/api/circuit-breaker — RM-67. */
export interface CircuitBreakerResponse {
  settings: CircuitBreakerValues;
  /** What .env asked for, so the UI can show what a reset would restore. */
  env_defaults: CircuitBreakerValues;
  is_overridden: boolean;
  /** Server-enforced ceiling on the recovery timeout, in seconds. */
  max_recovery_timeout: number;
}

const CIRCUIT_BREAKER_KEY = ["circuit-breaker"] as const;

export function useCircuitBreakerSettings() {
  return useQuery({
    queryKey: CIRCUIT_BREAKER_KEY,
    queryFn: async () => (await apiClient.get<CircuitBreakerResponse>("/circuit-breaker")).data,
  });
}

export function useUpdateCircuitBreakerSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (values: CircuitBreakerValues) =>
      (await apiClient.put<CircuitBreakerResponse>("/circuit-breaker", values)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CIRCUIT_BREAKER_KEY });
      // GET /admin/api/config serves the same values and is cached forever.
      queryClient.invalidateQueries({ queryKey: CONFIG_KEY });
    },
  });
}

export function useResetCircuitBreakerSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await apiClient.delete<CircuitBreakerResponse>("/circuit-breaker")).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CIRCUIT_BREAKER_KEY });
      queryClient.invalidateQueries({ queryKey: CONFIG_KEY });
    },
  });
}
