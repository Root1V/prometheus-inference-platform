import axios, { type InternalAxiosRequestConfig } from "axios";
import { clearStoredToken, getStoredToken } from "./auth";

/** Dispatched on window when a request 401s, so AuthContext can clear its state and redirect to login. */
export const AUTH_EXPIRED_EVENT = "prometheus:auth-expired";

function attachToken(config: InternalAxiosRequestConfig) {
  const token = getStoredToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
}

function handle401(error: unknown) {
  if (axios.isAxiosError(error) && error.response?.status === 401) {
    clearStoredToken();
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }
  return Promise.reject(error);
}

// import.meta.env.BASE_URL reflects vite.config.ts's `base` (set to "/admin/"),
// so this resolves to "/admin/api" — same-origin, no CORS needed.
export const apiClient = axios.create({
  baseURL: `${import.meta.env.BASE_URL}api`,
});
apiClient.interceptors.request.use(attachToken);
apiClient.interceptors.response.use((response) => response, handle401);

/**
 * Same auth/401 behavior as apiClient, but for gateway endpoints outside the
 * /admin/api namespace (e.g. GET /v1/usage) — same-origin, root-relative paths.
 */
export const rootClient = axios.create();
rootClient.interceptors.request.use(attachToken);
rootClient.interceptors.response.use((response) => response, handle401);

/**
 * PRM-236: the Playground, telling the gateway it is the Playground.
 *
 * Playground calls and SDK calls are the same credential hitting the same
 * route, so no path can separate them and the Activity page would label an
 * operator trying a model out as an integration running in production. The
 * header is self-reported — it says "the dashboard sent this", which is all
 * it is ever used for.
 */
export const playgroundClient = axios.create({
  headers: { "x-prometheus-source": "playground" },
});
playgroundClient.interceptors.request.use(attachToken);
playgroundClient.interceptors.response.use((response) => response, handle401);
