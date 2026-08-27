import axios from "axios";
import type { ProblemDetails } from "../types/api";

/** Extracts a human-readable message from an admin API error, preferring the RFC 9457 problem body. */
export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError<ProblemDetails>(error)) {
    const problem = error.response?.data;
    const detail = Array.isArray(problem?.detail)
      ? problem.detail.map((d) => d.msg).join("; ")
      : problem?.detail;
    if (problem?.title || detail) {
      return [problem?.title, detail].filter(Boolean).join(" — ");
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "Unknown error";
}
