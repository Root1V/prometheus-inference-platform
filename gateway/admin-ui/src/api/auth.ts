import axios from "axios";
import type { ProblemDetails } from "../types/api";

const TOKEN_STORAGE_KEY = "prometheus_admin_token";
const EXPIRY_STORAGE_KEY = "prometheus_admin_token_expires_at";

interface TokenResponse {
  access_token: string;
  expires_in: number;
}

/**
 * Exchanges an email and password for a JWT via the gateway's own
 * POST /admin/api/auth/login, which proxies to the auth-service server-side
 * (password grant — see gateway's admin/router.py). The SPA never calls the
 * auth-service directly — cross-origin browser requests to it are blocked by
 * CORS (auth-service doesn't run on the gateway's origin), and routing through
 * the gateway also means the SPA never needs to know the auth-service's URL at
 * all. Uses a bare axios call (not ./client's apiClient) to avoid a circular
 * import — client.ts imports getStoredToken/clearStoredToken from this module.
 *
 * PRM-249: the client_credentials grant is deliberately not reachable from
 * here. A client id and secret belong to a software integration, which gets
 * its token from the auth-service's token endpoint; a human signs in with the
 * email the admin issued them. The endpoint still accepts the other grant —
 * this is the dashboard declining to offer it, not the API dropping it.
 */
export function fetchAccessTokenWithPassword(
  email: string,
  password: string,
): Promise<TokenResponse> {
  return axios
    .post<TokenResponse>(`${import.meta.env.BASE_URL}api/auth/login`, {
      email,
      password,
    })
    .then((response) => response.data);
}

/**
 * PRM-249: the sign-in reply carries `expires_in` and nothing used to read it.
 *
 * On this deployment that is 10800 — three hours. The token was stored and the
 * clock ignored, so a session died without a word: the next call 401'd, the
 * reader was thrown to the login page with no explanation, and whatever they
 * had half-typed went with it. Storing the deadline lets the app see expiry
 * coming instead of discovering it from a failed request.
 */
export function storeToken(token: string, expiresInSeconds: number): void {
  sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
  sessionStorage.setItem(
    EXPIRY_STORAGE_KEY,
    String(Date.now() + expiresInSeconds * 1000),
  );
}

/** The stored token, or null once its own deadline has passed. */
export function getStoredToken(): string | null {
  const token = sessionStorage.getItem(TOKEN_STORAGE_KEY);
  if (token === null) return null;
  const expiresAt = getStoredExpiry();
  // A token kept past its deadline is only good for producing a 401, so a
  // reload three hours later lands on the login page instead of on a page
  // whose every panel fails.
  if (expiresAt !== null && Date.now() >= expiresAt) {
    clearStoredToken();
    return null;
  }
  return token;
}

/** Epoch milliseconds at which the stored token stops being accepted. */
export function getStoredExpiry(): number | null {
  const raw = sessionStorage.getItem(EXPIRY_STORAGE_KEY);
  if (raw === null) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

export function clearStoredToken(): void {
  sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  sessionStorage.removeItem(EXPIRY_STORAGE_KEY);
}

/**
 * PRM-249: what to put on the screen when a sign-in is refused.
 *
 * The auth-service answers both grants with one message, written for the
 * machine one: someone who typed their email and password read "Invalid
 * Credentials — Invalid client credentials." — doubled, because the generic
 * handler joins an RFC 9457 title to a detail that repeats it, and talking
 * about client credentials to a person who was never shown any. A 401 here has
 * exactly one meaning, so it gets one sentence; anything else still comes
 * through verbatim, because a misconfigured gateway or an unreachable
 * auth-service is a different problem and the operator needs to read it.
 */
export function describeLoginError(error: unknown): string {
  if (axios.isAxiosError<ProblemDetails>(error)) {
    if (error.response?.status === 401) {
      return "That email and password did not match an account. Check them and try again.";
    }
    const problem = error.response?.data;
    const detail = Array.isArray(problem?.detail)
      ? problem.detail.map((d) => d.msg).join("; ")
      : problem?.detail;
    if (detail) return detail;
    if (problem?.title) return problem.title;
    if (error.response === undefined) {
      return "The dashboard could not reach the gateway. It may be restarting.";
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "Unknown error";
}
