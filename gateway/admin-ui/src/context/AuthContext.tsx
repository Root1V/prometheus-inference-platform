import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  clearStoredToken,
  fetchAccessTokenWithPassword,
  getStoredExpiry,
  getStoredToken,
  storeToken,
} from "../api/auth";
import { AUTH_EXPIRED_EVENT } from "../api/client";

/**
 * PRM-249: why the reader is looking at the login page.
 *
 * "expired" and "signed-out" land on the same screen and mean opposite things
 * — one is the platform interrupting the operator, the other is the operator
 * closing the door behind them. The login page said nothing about either, so
 * an expiry read as a bug.
 */
export interface SessionEnd {
  reason: "expired" | "signed-out";
  /** The route they were on when it happened, so sign-in can put them back. */
  returnTo: string | null;
}

interface AuthContextValue {
  isAuthenticated: boolean;
  loginWithPassword: (email: string, password: string) => Promise<void>;
  logout: () => void;
  sessionEnded: SessionEnd | null;
  /** Epoch ms the current token stops working, or null when not signed in. */
  expiresAt: number | null;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

/** The hash route in view, or null when it is the login page itself. */
function currentRoute(): string | null {
  const route = window.location.hash.replace(/^#/, "");
  if (route === "" || route.startsWith("/login")) return null;
  return route;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => getStoredToken());
  const [expiresAt, setExpiresAt] = useState<number | null>(() =>
    getStoredToken() === null ? null : getStoredExpiry(),
  );
  const [sessionEnded, setSessionEnded] = useState<SessionEnd | null>(null);

  const endSession = useCallback((reason: SessionEnd["reason"]) => {
    clearStoredToken();
    setToken(null);
    setExpiresAt(null);
    setSessionEnded({
      reason,
      returnTo: reason === "expired" ? currentRoute() : null,
    });
  }, []);

  const logout = useCallback(() => endSession("signed-out"), [endSession]);

  // A 401 from any gateway admin API call means the token expired or was
  // revoked. Either way the operator did not ask to be signed out, so it is
  // recorded as an expiry and the route is kept.
  const expire = useCallback(() => endSession("expired"), [endSession]);
  useEffect(() => {
    window.addEventListener(AUTH_EXPIRED_EVENT, expire);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, expire);
  }, [expire]);

  /**
   * Meet the deadline instead of waiting for the failed request.
   *
   * Without this the first sign that a three-hour session had ended was a
   * panel full of errors, and the operator found out by losing something.
   */
  useEffect(() => {
    if (token === null || expiresAt === null) return;
    // Clamped, not branched: a deadline already in the past cannot reach here
    // (getStoredToken drops such a token, so `token` is null), and a timer is
    // the right answer anyway — a laptop that slept through the deadline wakes
    // up and fires it immediately.
    const timer = window.setTimeout(
      expire,
      Math.max(0, expiresAt - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [token, expiresAt, expire]);

  const loginWithPassword = useCallback(
    async (email: string, password: string) => {
      const { access_token, expires_in } = await fetchAccessTokenWithPassword(
        email,
        password,
      );
      storeToken(access_token, expires_in);
      setToken(access_token);
      setExpiresAt(getStoredExpiry());
      setSessionEnded(null);
    },
    [],
  );

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated: token !== null,
        loginWithPassword,
        logout,
        sessionEnded,
        expiresAt,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
