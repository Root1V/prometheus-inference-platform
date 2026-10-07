import { CircleCheck, CircleSlash, Clock, TriangleAlert } from "lucide-react";
import { cn } from "../lib/cn";
import type { Node } from "../types/node";

/**
 * What a node's state actually is — PRM-206.
 *
 * The page showed one badge driven by `is_active`, which the coordinator
 * derives from two facts it reports separately on purpose. `fleet.py` says why:
 * *"'cordoned' and 'not answering' need different actions from an operator and
 * a single boolean cannot tell them apart."* Before PRM-151 they shared one
 * column and a maintenance cordon was erased by the next health check.
 *
 * So there are three states here, not two, and they want three different
 * responses: leave it alone, go and look at the box, or press Activate.
 */

/** Matches `DEFAULT_LIVENESS_TTL_S` in `fleet.py`. Nodes heartbeat every 10s,
 * so a gap approaching a minute means six missed reports, not a slow one. */
const LIVENESS_TTL_S = 60;

type Liveness = "active" | "cordoned" | "unreachable" | "never-seen";

function livenessOf(node: Node): Liveness {
  if (!node.enabled) return "cordoned";
  if (!node.last_seen_at) return "never-seen";
  const ageS = (Date.now() - new Date(node.last_seen_at).getTime()) / 1000;
  return ageS < LIVENESS_TTL_S ? "active" : "unreachable";
}

function heartbeatAge(lastSeenAt: string | null): string {
  if (!lastSeenAt) return "never";
  const s = Math.max(0, Math.round((Date.now() - new Date(lastSeenAt).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86_400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86_400)}d ago`;
}

const STATES: Record<
  Liveness,
  { label: string; className: string; icon: typeof CircleCheck; hint: string }
> = {
  active: {
    label: "Active",
    icon: CircleCheck,
    className: "bg-green-100 text-green-800 dark:bg-green-500/15 dark:text-green-400",
    hint: "Allowed by the operator and reporting within the 60s liveness window.",
  },
  cordoned: {
    label: "Cordoned",
    icon: CircleSlash,
    className: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-400",
    hint: "Deactivated on purpose. It may still be perfectly healthy — nothing is routed to it until somebody presses Activate.",
  },
  unreachable: {
    label: "Not answering",
    icon: TriangleAlert,
    className: "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-400",
    hint: "Allowed by the operator but nothing has been heard from it inside the liveness window. This one needs looking at.",
  },
  "never-seen": {
    label: "Never seen",
    icon: Clock,
    className: "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-400",
    hint: "Registered but has never reported. Usually the three fleet identity variables are missing from its environment — see docs/local-stack.md.",
  },
};

export function NodeLivenessBadge({ node }: { node: Node }) {
  const state = livenessOf(node);
  const { label, className, icon: Icon, hint } = STATES[state];
  return (
    <div className="flex flex-col items-start gap-1">
      <span
        title={hint}
        className={cn(
          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
          className,
        )}
      >
        <Icon size={12} />
        {label}
      </span>
      {/* The heartbeat itself, because "when did we last hear from it" is the
          question this page exists to answer and it was readable only by
          opening fleet.db by hand. */}
      <span className="text-xs text-text-muted" title={node.last_seen_at ?? "No heartbeat recorded"}>
        {heartbeatAge(node.last_seen_at)}
      </span>
    </div>
  );
}
