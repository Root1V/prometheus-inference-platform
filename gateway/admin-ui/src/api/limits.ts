import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import { CONFIG_KEY } from "./config";

/** The six limits an operator can edit. `null` on a per-endpoint override
 * means "no override — use the global value". */
export interface RateLimitValues {
  rate_limit_rpm: number;
  rate_limit_tpm: number;
  rate_limit_rpm_chat_completions: number | null;
  rate_limit_tpm_chat_completions: number | null;
  rate_limit_rpm_admin: number | null;
  rate_limit_tpm_admin: number | null;
}

/** GET/PUT/DELETE /admin/api/limits — RM-56. */
export interface RateLimitsResponse {
  limits: RateLimitValues;
  /** What .env asked for, so the UI can show what a reset would restore. */
  env_defaults: RateLimitValues;
  /** false = the .env values are in effect verbatim, nothing saved. */
  is_overridden: boolean;
  /** Not editable here — a deployment decision, shown for context only. */
  rate_limit_strict: boolean;
  /** Floor the server enforces on the admin bucket, to keep the dashboard
   * itself reachable. */
  min_admin_rpm: number;
}

const LIMITS_KEY = ["rate-limits"] as const;

export function useRateLimits() {
  return useQuery({
    queryKey: LIMITS_KEY,
    queryFn: async () => (await apiClient.get<RateLimitsResponse>("/limits")).data,
  });
}

export function useUpdateRateLimits() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (values: RateLimitValues) =>
      (await apiClient.put<RateLimitsResponse>("/limits", values)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: LIMITS_KEY });
      // GET /admin/api/config serves the same live values and is cached with
      // staleTime: Infinity — without this it would keep showing the old ones.
      queryClient.invalidateQueries({ queryKey: CONFIG_KEY });
    },
  });
}

export function useResetRateLimits() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await apiClient.delete<RateLimitsResponse>("/limits")).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: LIMITS_KEY });
      queryClient.invalidateQueries({ queryKey: CONFIG_KEY });
    },
  });
}
