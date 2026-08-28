import { useQuery } from "@tanstack/react-query";
import { apiClient } from "./client";

/** GET /admin/api/sessions — docs/roadmap.md RM-23. */
export interface SessionEntry {
  client_id: string;
  user_id: string;
  /** "dashboard" (admin-ui) | "api" (direct /v1/* callers, incl. SDKs) | "other". */
  connection_type: "dashboard" | "api" | "other";
  last_seen_ago_s: number;
}

interface SessionsResponse {
  sessions: SessionEntry[];
}

const SESSIONS_KEY = ["gateway-sessions"] as const;
const POLL_INTERVAL_MS = 15000;

export function useSessions() {
  return useQuery({
    queryKey: SESSIONS_KEY,
    queryFn: async () => (await apiClient.get<SessionsResponse>("/sessions")).data.sessions,
    refetchInterval: POLL_INTERVAL_MS,
  });
}
