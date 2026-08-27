import type { LucideIcon } from "lucide-react";

interface StatCardProps {
  label: string;
  value: number | string;
  icon: LucideIcon;
  /** Optional secondary line under the value, e.g. "1 running · 27 stopped". */
  sub?: string;
}

export function StatCard({ label, value, icon: Icon, sub }: StatCardProps) {
  return (
    <div className="flex items-center gap-4 rounded-xl border border-border bg-surface p-5 shadow-sm">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
        <Icon size={20} />
      </div>
      <div>
        <p className="text-sm text-text-muted">{label}</p>
        <p className="text-2xl font-semibold text-text">{value}</p>
        {sub && <p className="text-xs text-text-muted">{sub}</p>}
      </div>
    </div>
  );
}
