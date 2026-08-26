import type { InstanceEntry } from "./instance";

export interface InstancesResponse {
  instances: InstanceEntry[];
  unreachable_nodes: string[];
}

/** RFC 9457 Problem Details, returned by the admin API on error responses. */
export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail: string;
}
