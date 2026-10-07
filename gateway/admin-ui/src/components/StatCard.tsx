import type { LucideIcon } from "lucide-react";
import { cn } from "../lib/cn";

/**
 * How a number should read at a glance — PRM-205.
 *
 * Before this every card was the same neutral grey, so a 33% error rate and
 * "2 of 2 nodes active" were visually identical and the landing page could not
 * tell you something was wrong.
 *
 * **`tone` is only ever set where the judgement is unambiguous**, and that
 * restraint is the design rather than a gap. Latency deliberately has none: a
 * p50 of twelve seconds is alarming for an embedding and entirely normal for a
 * 20B model writing three thousand tokens, and a threshold invented here would
 * manufacture alarms that teach people to ignore the colour.
 */
export type StatTone = "neutral" | "good" | "warn" | "bad";

const TONE_STYLES: Record<StatTone, { tile: string; value: string; card: string }> = {
  neutral: { tile: "bg-primary/10 text-primary", value: "text-text", card: "" },
  good: {
    tile: "bg-green-500/10 text-green-600 dark:text-green-400",
    value: "text-text",
    card: "",
  },
  warn: {
    tile: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
    value: "text-amber-700 dark:text-amber-400",
    card: "border-amber-400/40",
  },
  bad: {
    tile: "bg-red-500/15 text-red-600 dark:text-red-400",
    value: "text-red-700 dark:text-red-400",
    card: "border-red-400/50",
  },
};

interface StatCardProps {
  label: string;
  value: number | string;
  icon: LucideIcon;
  /** Optional secondary line under the value, e.g. "1 running · 27 stopped". */
  sub?: string;
  tone?: StatTone;
  /** Why this number is coloured the way it is. Shown on hover — a colour that
   * cannot explain itself is a question rather than an answer. */
  toneReason?: string;
}

export function StatCard({ label, value, icon: Icon, sub, tone = "neutral", toneReason }: StatCardProps) {
  const styles = TONE_STYLES[tone];
  return (
    <div
      title={toneReason}
      className={cn(
        "flex items-center gap-4 rounded-xl border border-border bg-surface p-5 shadow-sm",
        styles.card,
      )}
    >
      <div
        className={cn(
          "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg",
          styles.tile,
        )}
      >
        <Icon size={20} />
      </div>
      <div className="min-w-0">
        <p className="text-sm text-text-muted">{label}</p>
        <p className={cn("text-2xl font-semibold", styles.value)}>{value}</p>
        {sub && <p className="truncate text-xs text-text-muted">{sub}</p>}
      </div>
    </div>
  );
}
