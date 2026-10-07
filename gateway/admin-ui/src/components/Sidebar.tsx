import {
  Coins,
  Database,
  Flame,
  Gauge,
  HardDrive,
  LayoutDashboard,
  LogOut,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Radio,
  Receipt,
  Server,
  Sun,
  Terminal,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { NavLink } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useTheme, type ThemeMode } from "../context/ThemeContext";
import { cn } from "../lib/cn";

const NAV_ITEMS = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/instances", label: "Instances", icon: Server },
  { to: "/models", label: "Models", icon: Database },
  { to: "/nodes", label: "Nodes", icon: HardDrive },
  { to: "/playground", label: "Playground", icon: Terminal },
  { to: "/usage", label: "Usage", icon: Coins },
  { to: "/billing", label: "Billing", icon: Receipt },
  { to: "/limits", label: "Limits", icon: Gauge },
  { to: "/sessions", label: "Sessions", icon: Radio },
  { to: "/users", label: "Users", icon: Users },
];

const THEME_OPTIONS: { mode: ThemeMode; label: string; icon: typeof Sun }[] = [
  { mode: "light", label: "Light", icon: Sun },
  { mode: "system", label: "Match system", icon: Monitor },
  { mode: "dark", label: "Dark", icon: Moon },
];

const STORAGE_KEY = "prometheus.sidebar.collapsed";

/**
 * PRM-201. Persisted because a layout preference that resets on every
 * navigation is not a preference. Read lazily so the first paint is already
 * the chosen width — reading it in an effect would render expanded and then
 * snap, which looks like a bug on every page load.
 *
 * Wrapped because `localStorage` throws outright in a private window and in
 * an iframe with third-party storage blocked, and a dashboard that will not
 * mount because it could not remember a sidebar width is a bad trade.
 */
function useCollapsed(): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(collapsed));
    } catch {
      /* no persistence available; the session still works */
    }
  }, [collapsed]);

  return [collapsed, useCallback(() => setCollapsed((c) => !c), [])];
}

/**
 * The label that appears beside a collapsed icon.
 *
 * `title` alone was the obvious choice and is the wrong one: the native
 * tooltip waits about a second, cannot be styled to match, and never appears
 * for a keyboard user. This shows immediately on hover *and* on focus, which
 * is the case that matters — tabbing through a rail of ten identical-sized
 * icons with no labels is otherwise unusable.
 *
 * **Rendered into `document.body`, and that is not over-engineering.** The
 * first version positioned it `absolute` beside the icon and it never appeared:
 * the nav scrolls, `overflow-y: auto` computes `overflow-x: auto` as well, and
 * the nav's right edge is the rail's 64px. Measured in the running page — the
 * tooltip's box ran to 134px and was clipped at 64. Any ancestor that scrolls
 * does this, so moving the scroll to the `aside` only relocates the bug. A
 * portal leaves the clipping context entirely; the position is read from the
 * item at the moment it is shown, so it stays correct while the rail scrolls.
 *
 * `aria-hidden`: the link already carries its name for assistive tech, so
 * announcing this too would read every item twice.
 */
function Rail({ label, children }: { label: string; children: ReactNode }) {
  const anchor = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);

  const show = () => {
    const box = anchor.current?.getBoundingClientRect();
    if (box) setAt({ top: box.top + box.height / 2, left: box.right + 8 });
  };

  return (
    <div
      ref={anchor}
      className="relative"
      onMouseEnter={show}
      onMouseLeave={() => setAt(null)}
      onFocus={show}
      onBlur={() => setAt(null)}
    >
      {children}
      {at &&
        createPortal(
          <span
            aria-hidden
            style={{ top: at.top, left: at.left }}
            className="pointer-events-none fixed z-50 -translate-y-1/2 whitespace-nowrap rounded-md bg-gray-800 px-2 py-1 text-xs font-medium text-gray-100 shadow-lg"
          >
            {label}
          </span>,
          document.body,
        )}
    </div>
  );
}

export function Sidebar() {
  const { logout } = useAuth();
  const { mode, setMode } = useTheme();
  const [collapsed, toggle] = useCollapsed();

  // Collapsed, the three-way theme control has no room for three targets that
  // are still large enough to hit. One button that cycles keeps the choice
  // reachable without shrinking it into a 14px sliver.
  const currentTheme = THEME_OPTIONS.find((o) => o.mode === mode) ?? THEME_OPTIONS[1];
  const cycleTheme = () => {
    const i = THEME_OPTIONS.findIndex((o) => o.mode === mode);
    setMode(THEME_OPTIONS[(i + 1) % THEME_OPTIONS.length].mode);
  };

  const wrap = (label: string, node: ReactNode) =>
    collapsed ? <Rail label={label}>{node}</Rail> : node;

  return (
    <aside
      className={cn(
        "sticky top-0 flex h-screen shrink-0 flex-col bg-gray-900 text-gray-100 transition-[width] duration-200",
        collapsed ? "w-16" : "w-60",
      )}
    >
      <div className={cn("flex items-center py-5", collapsed ? "justify-center px-0" : "gap-2 px-5")}>
        <Flame size={20} className="shrink-0 text-primary" />
        {!collapsed && (
          <div className="min-w-0">
            <p className="truncate text-base font-semibold text-white">Prometheus</p>
            <p className="truncate text-xs text-gray-400">Inference Admin</p>
          </div>
        )}
      </div>

      <nav className={cn("flex-1 space-y-1 overflow-y-auto", collapsed ? "px-2" : "px-3")}>
        {NAV_ITEMS.map(({ to, label, icon: Icon }) =>
          wrap(
            label,
            <NavLink
              key={to}
              to={to}
              end
              aria-label={label}
              className={({ isActive }) =>
                cn(
                  "flex items-center rounded-lg text-sm font-medium transition-colors",
                  collapsed ? "justify-center p-2.5" : "gap-3 px-3 py-2",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-gray-300 hover:bg-gray-800 hover:text-white",
                )
              }
            >
              <Icon size={18} className="shrink-0" />
              {!collapsed && label}
            </NavLink>,
          ),
        )}
      </nav>

      <div className={cn("space-y-2 border-t border-gray-800 py-3", collapsed ? "px-2" : "px-3")}>
        {collapsed ? (
          <Rail label={`Theme: ${currentTheme.label}`}>
            <button
              type="button"
              onClick={cycleTheme}
              aria-label={`Theme: ${currentTheme.label}. Change theme`}
              className="flex w-full items-center justify-center rounded-lg p-2.5 text-gray-400 transition-colors hover:bg-gray-800 hover:text-white"
            >
              <currentTheme.icon size={18} />
            </button>
          </Rail>
        ) : (
          <div
            role="radiogroup"
            aria-label="Theme"
            className="flex items-center gap-1 rounded-lg bg-gray-800 p-1"
          >
            {THEME_OPTIONS.map(({ mode: optionMode, label, icon: Icon }) => (
              <button
                key={optionMode}
                type="button"
                role="radio"
                aria-checked={mode === optionMode}
                title={label}
                onClick={() => setMode(optionMode)}
                className={cn(
                  "flex flex-1 items-center justify-center rounded-md py-1.5 transition-colors",
                  mode === optionMode
                    ? "bg-gray-700 text-white"
                    : "text-gray-400 hover:text-gray-200",
                )}
              >
                <Icon size={16} />
                <span className="sr-only">{label}</span>
              </button>
            ))}
          </div>
        )}

        {wrap(
          collapsed ? "Expand sidebar" : "Collapse sidebar",
          <button
            type="button"
            onClick={toggle}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-expanded={!collapsed}
            className={cn(
              "flex w-full items-center rounded-lg text-sm font-medium text-gray-400 transition-colors hover:bg-gray-800 hover:text-white",
              collapsed ? "justify-center p-2.5" : "gap-3 px-3 py-2",
            )}
          >
            {collapsed ? (
              <PanelLeftOpen size={18} className="shrink-0" />
            ) : (
              <PanelLeftClose size={18} className="shrink-0" />
            )}
            {!collapsed && "Collapse"}
          </button>,
        )}

        {wrap(
          "Logout",
          <button
            type="button"
            onClick={logout}
            aria-label="Logout"
            className={cn(
              "flex w-full items-center rounded-lg text-sm font-medium text-gray-300 transition-colors hover:bg-gray-800 hover:text-white",
              collapsed ? "justify-center p-2.5" : "gap-3 px-3 py-2",
            )}
          >
            <LogOut size={18} className="shrink-0" />
            {!collapsed && "Logout"}
          </button>,
        )}
      </div>
    </aside>
  );
}
