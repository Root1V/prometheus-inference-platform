import { Play, Square } from "lucide-react";
import { useState } from "react";
import { useStartInstance, useStopInstance } from "../api/instances";
import { useToast } from "../context/ToastContext";
import { getErrorMessage } from "../lib/errors";
import type { InstanceEntry } from "../types/instance";

/**
 * Start or stop several instances at once — PRM-204.
 *
 * Restarting a node's models after a config change meant clicking through them
 * one row at a time, and on this deployment that is twelve rows.
 *
 * **Sequential, not parallel, and that is deliberate.** Each start loads weights
 * — the largest here is 20 GB — so firing twelve at once would have the machine
 * swapping rather than serving. It also means a failure stops the rest, which
 * is the behaviour you want when the reason the second one failed is that the
 * first one took the memory.
 *
 * Reports what happened rather than claiming success: a partial run says how
 * many of how many, because "4 started" when you selected six is the kind of
 * quiet half-success this codebase keeps finding.
 */
export function BulkActions({
  instances,
  onDone,
}: {
  instances: InstanceEntry[];
  onDone: () => void;
}) {
  const { showToast } = useToast();
  const start = useStartInstance();
  const stop = useStopInstance();
  const [running, setRunning] = useState<"start" | "stop" | null>(null);

  const run = async (action: "start" | "stop") => {
    const mutate = action === "start" ? start.mutateAsync : stop.mutateAsync;
    const targets =
      action === "start"
        ? instances.filter((i) => i.state !== "ready" && i.state !== "loading")
        : instances.filter((i) => i.state !== "stopped");

    if (targets.length === 0) {
      showToast(`Nothing to ${action} — the selection is already ${action === "start" ? "running" : "stopped"}.`, "success");
      onDone();
      return;
    }

    setRunning(action);
    let done = 0;
    let failure: string | null = null;
    for (const instance of targets) {
      try {
        await mutate({ node: instance.node, modelId: instance.id });
        done += 1;
      } catch (error) {
        failure = `${instance.id}: ${getErrorMessage(error)}`;
        break;
      }
    }
    setRunning(null);
    onDone();

    if (failure) {
      showToast(
        `${done} of ${targets.length} ${action === "start" ? "started" : "stopped"}, then stopped at ${failure}`,
        "error",
      );
    } else {
      showToast(`${done} instance${done === 1 ? "" : "s"} ${action === "start" ? "started" : "stopped"}`, "success");
    }
  };

  const buttonClass =
    "flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40";

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        disabled={running !== null}
        onClick={() => run("start")}
        className={`${buttonClass} text-green-700 hover:bg-green-50 dark:text-green-400 dark:hover:bg-green-950/40`}
      >
        <Play size={14} />
        {running === "start" ? "Starting…" : "Start"}
      </button>
      <button
        type="button"
        disabled={running !== null}
        onClick={() => run("stop")}
        className={`${buttonClass} text-text hover:bg-background`}
      >
        <Square size={14} />
        {running === "stop" ? "Stopping…" : "Stop"}
      </button>
    </div>
  );
}
