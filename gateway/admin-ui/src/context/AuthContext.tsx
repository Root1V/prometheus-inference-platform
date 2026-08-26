import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import {
  clearStoredToken,
  fetchAccessToken,
  fetchAccessTokenWithPassword,
  getStoredToken,
  storeToken,
} from "../api/auth";
import { AUTH_EXPIRED_EVENT } from "../api/client";

interface AuthContextValue {
  isAuthenticated: boolean;
  login: (clientId: string, clientSecret: string) => Promise<void>;
  loginWithPassword: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => getStoredToken());

  const logout = useCallback(() => {
    clearStoredToken();
    setToken(null);
  }, []);

  // A 401 from any gateway admin API call means the token expired or was revoked.
  useEffect(() => {
    window.addEventListener(AUTH_EXPIRED_EVENT, logout);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, logout);
  }, [logout]);

  const login = useCallback(async (clientId: string, clientSecret: string) => {
    const { access_token } = await fetchAccessToken(clientId, clientSecret);
    storeToken(access_token);
    setToken(access_token);
  }, []);

  const loginWithPassword = useCallback(async (email: string, password: string) => {
    const { access_token } = await fetchAccessTokenWithPassword(email, password);
    storeToken(access_token);
    setToken(access_token);
  }, []);

  return (
    <AuthContext.Provider value={{ isAuthenticated: token !== null, login, loginWithPassword, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
