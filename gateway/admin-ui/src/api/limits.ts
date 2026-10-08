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
/** PRM-229: one limit, with the layer it belongs to and whether this page can
 *  edit it. The eleven added by PRM-224..228 are `.env`-only until the
 *  migration that gives `RateLimitConfig` a column each. */
export interface LimitField {
  field: string;
  /** What is in force, which is not always what is configured. */
  value: number | null;
  /**
   * `set` — a number someone chose.
   * `derived` — computed by PRM-227 and enforced all the same.
   * `unset` — this dimension genuinely refuses nothing.
   *
   * The middle one is why this field exists: reporting the raw setting would
   * have the page call an enforced 360 RPM ceiling "nothing set".
   */
  source: "set" | "derived" | "unset";
  editable: boolean;
}

export interface LimitLayer {
  layer: "platform" | "client" | "endpoint";
  /** One line on what this layer bounds — the page shows it verbatim. */
  what: string;
  fields: LimitField[];
}

export interface RateLimitsResponse {
  limits: RateLimitValues;
  /** PRM-229: every limit there is, grouped by layer. A request passes all
   *  three, so showing one of them was showing a third of the answer. */
  layers: LimitLayer[];
  /** What .env asked for, so the UI can show what a reset would restore. */
  env_defaults: RateLimitValues;
  /** false = the .env values are in effect verbatim, nothing saved. */
  is_overridden: boolean;
  /** Not editable here — a deployment decision, shown for context only. */
  rate_limit_strict: boolean;
  /** Floor the server enforces on the admin bucket, to keep the dashboard
   * itself reachable. */
  min_admin_rpm: number;
  /** RM-71: concurrent slots the running backends actually report. Shown beside
   * the limit so it isn't set blind; never used to derive one. `reporting` is
   * how many backends the number covers — engines like sd.cpp report none. */
  capacity: { slots: number | null; reporting: number };
}

const LIMITS_KEY = ["rate-limits"] as const;

export function useRateLimits() {
  return useQuery({
    queryKey: LIMITS_KEY,
    queryFn: async () =>
      (await apiClient.get<RateLimitsResponse>("/limits")).data,
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
    mutationFn: async () =>
      (await apiClient.delete<RateLimitsResponse>("/limits")).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: LIMITS_KEY });
      queryClient.invalidateQueries({ queryKey: CONFIG_KEY });
    },
  });
}

// ── PRM-230: which ceiling is refusing right now ─────────────────────────────

/** One counter standing in the current bucket, against the ceiling that reads
 *  it. `limit: null` is not "unlimited" — it is "no check reads this counter",
 *  which the page prints rather than hides. */
export interface LiveCounter {
  dimension: "rpm" | "tpm" | "tpm_in" | "tpm_out" | "rpd" | "tpd" | "ipm";
  identity: string;
  endpoint: string;
  window: "minute" | "day";
  used: number;
  layer: "platform" | "client" | "endpoint";
  limit: number | null;
  /** `tier` — this client's tier chose it. `set` — a configured number.
   *  `derived` — PRM-227's sum of the per-endpoint allowances. `none` — nothing
   *  checks this counter. */
  limit_source: "tier" | "set" | "derived" | "none";
  /** Null where there is no ceiling. Not 0, which would sort with the idle
   *  rows and read as headroom. */
  percent: number | null;
}

export interface LiveLimitsResponse {
  available: boolean;
  /** Why there is nothing to show, when there isn't. */
  reason: string | null;
  /** Closest to refusing first; unmetered counters last. */
  rows: LiveCounter[];
  minute_resets_in?: number;
}

export function useLiveLimits() {
  return useQuery({
    queryKey: ["rate-limits", "live"],
    queryFn: async () =>
      (await apiClient.get<LiveLimitsResponse>("/limits/live")).data,
    // The counters are per-minute buckets, so a stale read is a wrong answer
    // about whether something is being refused *now*. Ten seconds is often
    // enough to watch a budget fill and rare enough not to be the traffic.
    refetchInterval: 10_000,
  });
}
