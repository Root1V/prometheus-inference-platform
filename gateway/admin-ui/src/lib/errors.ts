import axios from "axios";
import type { ProblemDetails } from "../types/api";

/** Extracts a human-readable message from an admin API error, preferring the RFC 9457 problem body. */
export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError<ProblemDetails>(error)) {
    const problem = error.response?.data;
    if (problem?.title || problem?.detail) {
      return [problem.title, problem.detail].filter(Boolean).join(" — ");
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "Unknown error";
}
