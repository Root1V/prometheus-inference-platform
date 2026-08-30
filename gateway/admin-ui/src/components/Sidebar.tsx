import {
  Coins,
  Database,
  Flame,
  Gauge,
  HardDrive,
  LayoutDashboard,
  LogOut,
  Radio,
  Server,
  Terminal,
  Users,
} from "lucide-react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { cn } from "../lib/cn";

const NAV_ITEMS = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/instances", label: "Instances", icon: Server },
  { to: "/models", label: "Models", icon: Database },
  { to: "/nodes", label: "Nodes", icon: HardDrive },
  { to: "/playground", label: "Playground", icon: Terminal },
  { to: "/usage", label: "Usage", icon: Coins },
  { to: "/limits", label: "Limits", icon: Gauge },
  { to: "/sessions", label: "Sessions", icon: Radio },
  { to: "/users", label: "Users", icon: Users },
];

export function Sidebar() {
  const { logout } = useAuth();

  return (
    <aside className="sticky top-0 flex h-screen w-64 shrink-0 flex-col overflow-y-auto bg-gray-900 text-gray-100">
      <div className="px-6 py-6">
        <div className="flex items-center gap-2">
          <Flame size={20} className="text-primary" />
          <p className="text-lg font-semibold text-white">Prometheus</p>
        </div>
        <p className="text-xs text-gray-400">Inference Admin</p>
      </div>
      <nav className="flex-1 space-y-1 px-3">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-primary text-primary-foreground"
                  : "text-gray-300 hover:bg-gray-800 hover:text-white",
              )
            }
          >
            <Icon size={18} />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-gray-800 p-3">
        <button
          type="button"
          onClick={logout}
          className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white"
        >
          <LogOut size={18} />
          Logout
        </button>
      </div>
    </aside>
  );
}
