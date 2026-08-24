import axios from "axios";
import { clearStoredToken, getStoredToken } from "./auth";

/** Dispatched on window when a request 401s, so AuthContext can clear its state and redirect to login. */
export const AUTH_EXPIRED_EVENT = "prometheus:auth-expired";

// import.meta.env.BASE_URL reflects vite.config.ts's `base` (set to "/admin/"),
// so this resolves to "/admin/api" — same-origin, no CORS needed.
export const apiClient = axios.create({
  baseURL: `${import.meta.env.BASE_URL}api`,
});

apiClient.interceptors.request.use((config) => {
  const token = getStoredToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      clearStoredToken();
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    }
    return Promise.reject(error);
  },
);
