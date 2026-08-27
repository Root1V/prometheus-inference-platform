import type { InstanceEntry } from "./instance";

export interface InstancesResponse {
  instances: InstanceEntry[];
  unreachable_nodes: string[];
}

/** A single FastAPI/pydantic request-validation error entry. */
export interface ValidationErrorDetail {
  loc: (string | number)[];
  msg: string;
  type: string;
}

/**
 * Error responses seen from the admin API. Most routes return RFC 9457
 * Problem Details (`detail` a string) via the app's own `_problem()`/
 * `HTTPException` handling. But a Pydantic request-body validation failure
 * (e.g. a malformed field caught by a model validator) never reaches that
 * code — FastAPI's built-in exception handler answers first, with `detail`
 * as an array of `ValidationErrorDetail` instead of a string.
 */
export interface ProblemDetails {
  type?: string;
  title?: string;
  status?: number;
  detail: string | ValidationErrorDetail[];
}
