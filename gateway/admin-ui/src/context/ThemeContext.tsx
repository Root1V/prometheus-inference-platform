import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

export type ThemeMode = "light" | "dark" | "system";
type ResolvedTheme = "light" | "dark";

interface ThemeContextValue {
  /** The operator's stored preference — "system" means "follow the OS." */
  mode: ThemeMode;
  /** What's actually applied right now — always light or dark, never "system". */
  resolvedTheme: ResolvedTheme;
  setMode: (mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

// Keep in sync with the inline anti-flash script in index.html.
const STORAGE_KEY = "prometheus-theme";
const DARK_MEDIA_QUERY = "(prefers-color-scheme: dark)";

function isThemeMode(value: string | null): value is ThemeMode {
  return value === "light" || value === "dark" || value === "system";
}

function getStoredMode(): ThemeMode {
  const stored = localStorage.getItem(STORAGE_KEY);
  return isThemeMode(stored) ? stored : "system";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(getStoredMode);
  // The OS preference is the one piece of state that's a genuine external
  // subscription (it changes from outside React, not from a prop/state
  // change), so it's the only part that belongs in an effect. Kept even
  // when mode !== "system" — simpler than mounting/unmounting the listener,
  // and it's just unused in that case.
  const [systemPrefersDark, setSystemPrefersDark] = useState(
    () => window.matchMedia(DARK_MEDIA_QUERY).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(DARK_MEDIA_QUERY);
    const handleChange = (e: MediaQueryListEvent) => setSystemPrefersDark(e.matches);
    mql.addEventListener("change", handleChange);
    return () => mql.removeEventListener("change", handleChange);
  }, []);

  const resolvedTheme: ResolvedTheme =
    mode === "system" ? (systemPrefersDark ? "dark" : "light") : mode;

  // Applies whenever the resolved theme changes, regardless of why (an
  // explicit choice, or the OS theme changing while mode === "system").
  useEffect(() => {
    document.documentElement.classList.toggle("dark", resolvedTheme === "dark");
  }, [resolvedTheme]);

  const setMode = useCallback((next: ThemeMode) => {
    localStorage.setItem(STORAGE_KEY, next);
    setModeState(next);
  }, []);

  return (
    <ThemeContext.Provider value={{ mode, resolvedTheme, setMode }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
