import axios from "axios";

const TOKEN_STORAGE_KEY = "prometheus_admin_token";

interface TokenResponse {
  access_token: string;
  expires_in: number;
}

/**
 * Exchanges credentials for a JWT via the gateway's own
 * POST /admin/api/auth/login, which proxies to the auth-service server-side
 * (client_credentials or password grant, depending on which fields are sent —
 * see gateway's admin/router.py). The SPA never calls the auth-service
 * directly — cross-origin browser requests to it are blocked by CORS
 * (auth-service doesn't run on the gateway's origin), and routing through the
 * gateway also means the SPA never needs to know the auth-service's URL at
 * all. Uses a bare axios call (not ./client's apiClient) to avoid a circular
 * import — client.ts imports getStoredToken/clearStoredToken from this
 * module.
 */
async function postLogin(body: Record<string, string>): Promise<TokenResponse> {
  const response = await axios.post<TokenResponse>(`${import.meta.env.BASE_URL}api/auth/login`, body);
  return response.data;
}

export function fetchAccessToken(clientId: string, clientSecret: string): Promise<TokenResponse> {
  return postLogin({ client_id: clientId, client_secret: clientSecret });
}

export function fetchAccessTokenWithPassword(email: string, password: string): Promise<TokenResponse> {
  return postLogin({ email, password });
}

export function storeToken(token: string): void {
  sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function getStoredToken(): string | null {
  return sessionStorage.getItem(TOKEN_STORAGE_KEY);
}

export function clearStoredToken(): void {
  sessionStorage.removeItem(TOKEN_STORAGE_KEY);
}
