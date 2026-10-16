import { X } from "lucide-react";
import { useEffect } from "react";
import { createPortal } from "react-dom";
import { cn } from "../lib/cn";
import { engineDisagreement, engineLabel } from "../lib/engines";
import { timeAgo } from "../lib/format";
import type { DeviceProfile, EngineStatus, Node } from "../types/node";

/**
 * What a node is, not just that it is up — PRM-270, PRM-264.
 *
 * PRM-263 made the agent read its own machine and send it with the heartbeat,
 * and the coordinator stored it. `/admin/api/nodes` has been returning it ever
 * since and this panel rendered none of it: the only way to see a node's
 * hardware was to open fleet.db by hand, which is the state PRM-206 already
 * fixed once for the heartbeat itself.
 *
 * Three things this deliberately does NOT do:
 *
 * - It never shows a reading without its age. The agent caches both for 5
 *   minutes and a node that stopped reporting leaves its last ones behind
 *   looking perfectly current.
 * - It never hides `probe_errors`, or the reason an engine is absent.
 *   profile.py: *"an empty profile with no explanation is the shape that gets
 *   read as 'this machine has nothing'"* — a view that swallows them
 *   reintroduces exactly that, and reads as a confident answer.
 * - It says nothing about which models fit. That is PRM-265, and deriving it
 *   here would collapse a reading into a verdict, which is the separation
 *   PRM-263 was built to keep.
 *
 * The layout earns its space the same way: the two numbers that decide
 * anything — free memory and free disk — get a bar, and the eight identity
 * fields that are read once get a single line. Absolute paths are shown as the
 * directory they live in, with the whole thing on hover: `/Users/<name>/.local/bin`
 * and `/Users/<name>/.cargo/bin` differ in the part that fits, and the first
 * version of this panel let a 60-character path wrap over two lines above the
 * engine it belonged to.
 */

function gb(mb: number | null): string {
  if (mb === null) return "unknown";
  return mb >= 1024 ? `${(mb / 1024).toFixed(mb >= 10_240 ? 0 : 1)} GB` : `${mb} MB`;
}

function cores(profile: DeviceProfile): string | null {
  const { cpu_cores_physical: physical, cpu_cores_logical: logical } = profile;
  if (physical === null && logical === null) return null;
  if (physical !== null && logical !== null && physical !== logical) {
    return `${physical} cores · ${logical} threads`;
  }
  return `${physical ?? logical} cores`;
}

/** `kind` is the node's own answer, and "none" means it looked — which is a
 * different sentence from "we did not look", so they read differently here.
 *
 * On a unified-memory Mac the accelerator's name *is* the CPU's, and printing
 * "Apple M4 Max" twice in four lines reads as a rendering bug. The kind is
 * what is left to say in that case, and on an Intel iMac the name is a real
 * second fact ("Intel Iris Pro Graphics 6200" beside an i5-5675R). */
function accelerator(profile: DeviceProfile): string {
  const accel = profile.accelerator;
  if (!accel) return "Accelerator not probed";
  if (accel.kind === "none") return "No accelerator found";
  const sameAsCpu = accel.name !== null && accel.name === profile.cpu_model;
  const parts = [sameAsCpu || accel.name === null ? accel.kind : accel.name];
  if (accel.memory_mb !== null) parts.push(gb(accel.memory_mb));
  if (accel.unified_memory) parts.push("unified memory");
  return parts.join(" · ");
}

/** A path with the user's home collapsed to `~`. The informative part of
 * `/Users/<name>/.local/bin/llama-server` is the tail — a system install, a
 * virtualenv and a cargo bin differ exactly there, and the first 25 characters
 * are the same on every row. The whole path stays on hover. */
function homeRelative(path: string): string {
  const home = path.match(/^\/(?:Users|home)\/[^/]+(\/.*)?$/);
  return home ? `~${home[1] ?? ""}` : path;
}

/** The directory an engine was found in, which is the part worth comparing
 * between rows — the file name is the engine's own name repeated. */
function homeRelativeDir(path: string): string {
  const cut = path.lastIndexOf("/");
  return homeRelative(cut > 0 ? path.slice(0, cut) : path);
}

function Meter({
  label,
  freeMb,
  totalMb,
  caption,
}: {
  label: string;
  freeMb: number | null;
  totalMb: number | null;
  caption?: string | null;
}) {
  const usedFraction =
    freeMb !== null && totalMb !== null && totalMb > 0 ? 1 - freeMb / totalMb : null;

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs font-medium text-text-muted">{label}</span>
        <span className="text-sm text-text">
          <span className="font-medium">{gb(freeMb)}</span>
          <span className="text-text-muted"> free of {gb(totalMb)}</span>
        </span>
      </div>
      {/* Neutral on purpose. capacity.py's 85/95 thresholds are about RAM
          *projected after adding a model*, so colouring current usage with
          them would answer a question nobody asked — and PRM-265's, wrongly. */}
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-border">
        {usedFraction !== null && (
          <div
            className="h-full rounded-full bg-primary/70"
            style={{ width: `${Math.min(100, Math.max(0, usedFraction * 100))}%` }}
          />
        )}
      </div>
      {caption && (
        <p className="mt-1 truncate text-xs text-text-muted" title={caption}>
          measured at {homeRelative(caption)}
        </p>
      )}
    </div>
  );
}

/** PRM-271: four states, because "installed" and "could run here" are
 * independent questions and this row used to answer only the first.
 *
 * `ruled-out` is the one that earns it. On an Intel iMac the old row said
 * "mlx_lm.server is not on PATH" — true, and it reads as *install it and you
 * will have MLX*. There is no MLX to install on x86_64. PRM-133 wrote the rule
 * this implements — *"an option that cannot work must not look like one that
 * can"* — and left the fact in a Python docstring where no operator saw it.
 *
 * `stranded` is rare and deliberately not hidden: an engine present on
 * hardware that cannot run it is a real thing to be told about, and a probe
 * that skipped it would report it as merely absent.
 */
type EngineState = "installed" | "stranded" | "installable" | "ruled-out" | "unknown";

function engineState(engine: EngineStatus): EngineState {
  if (engine.installed) return engine.applicable === false ? "stranded" : "installed";
  if (engine.applicable === false) return "ruled-out";
  if (engine.applicable === null) return "unknown";
  return "installable";
}

const DOT: Record<EngineState, string> = {
  installed: "bg-green-500",
  stranded: "bg-amber-500",
  // Hollow: something could go here. A filled dot would read as a state the
  // node is in rather than one it could reach.
  installable: "border border-text-muted/60",
  "ruled-out": "bg-border",
  unknown: "bg-border",
};

function EngineRow({ engine }: { engine: EngineStatus }) {
  const state = engineState(engine);
  const present = state === "installed" || state === "stranded";

  // The right column answers what follows "is it installed": where it came
  // from, what to do about it, or why there is nothing to do. `min-w-0` is
  // what lets the truncation actually happen in a flex row.
  const detail =
    state === "installed"
      ? homeRelativeDir(engine.path!)
      : state === "stranded"
        ? `installed, needs ${engine.requires}`
        : state === "ruled-out"
          ? `needs ${engine.requires}`
          : state === "unknown"
            ? `needs ${engine.requires} — unverified`
            : "not installed";

  return (
    <li className="flex items-center gap-2.5 py-1">
      <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", DOT[state])} />
      <span
        className={cn(
          "shrink-0 text-sm",
          present ? "text-text" : "text-text-muted",
          state === "ruled-out" && "opacity-60",
        )}
      >
        {engineLabel(engine.backend)}
      </span>
      <span
        className={cn(
          "ml-auto min-w-0 truncate text-right font-mono text-xs",
          state === "stranded" ? "text-amber-700 dark:text-amber-400" : "text-text-muted",
          state === "ruled-out" && "opacity-60",
        )}
        title={
          state === "stranded"
            ? `Installed at ${engine.path}, but this node cannot run it — needs ${engine.requires}`
            : state === "installed"
              ? engine.path!
              : (engine.reason ?? undefined)
        }
      >
        {detail}
      </span>
    </li>
  );
}

export function NodeProfileModal({ node, onClose }: { node: Node; onClose: () => void }) {
  const profile = node.profile;
  const disagreement = engineDisagreement(node);

  // Installed first. The five a node has are the answer and the three it does
  // not are the footnote, and `BACKENDS` order interleaves them.
  // Installed, then what could be added, then what the hardware rules out.
  // `BACKENDS` order interleaves all three, and the middle group is the only
  // one an operator can act on — it should not be hunted for.
  const ORDER: Record<EngineState, number> = {
    installed: 0,
    stranded: 1,
    installable: 2,
    unknown: 3,
    "ruled-out": 4,
  };
  const engines = node.engines_detected
    ? [...node.engines_detected].sort((a, b) => ORDER[engineState(a)] - ORDER[engineState(b)])
    : null;

  // PRM-271's answer in one line. "3 could be added" is the recommendation
  // this panel existed without: the list said what is here, and left what
  // could be here to be worked out by elimination.
  const tally = engines && {
    installed: engines.filter((e) => engineState(e) === "installed").length,
    installable: engines.filter((e) => engineState(e) === "installable").length,
    ruledOut: engines.filter((e) => engineState(e) === "ruled-out").length,
  };

  // Escape closes, and so does the backdrop. Neither is true of the other
  // modals on this page — deliberately not changed here, because they are
  // forms and dismissing one on a stray click outside discards typing. This
  // panel only reads, so the cheap dismissal is the right one for it.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // The CPU is the card's heading, so it is not repeated here.
  const identity = profile
    ? [cores(profile), profile.arch, `${profile.os} ${profile.os_release}`]
        .filter(Boolean)
        .join(" · ")
    : null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-8"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="max-h-full w-full max-w-lg overflow-y-auto rounded-xl bg-surface p-6 shadow-lg"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`Hardware profile for ${node.name}`}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-semibold text-text">{node.name}</h2>
            {/* The age sits under the title because it qualifies everything
                below it, not one field. */}
            <p className="text-xs text-text-muted">
              {profile ? (
                <span title={node.profile_seen_at ?? undefined}>
                  Reported {timeAgo(node.profile_seen_at)}
                </span>
              ) : (
                "No profile reported"
              )}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="shrink-0">
            <X size={18} className="text-text-muted" />
          </button>
        </div>

        {profile ? (
          <>
            <div className="mt-4 rounded-lg bg-background p-3">
              <p className="text-sm font-medium text-text">{profile.cpu_model ?? profile.arch}</p>
              <p className="mt-0.5 text-xs text-text-muted">{identity}</p>
              <p className="mt-0.5 text-xs text-text-muted">{accelerator(profile)}</p>
            </div>

            <div className="mt-4 space-y-3.5">
              <Meter label="Memory" freeMb={profile.ram_available_mb} totalMb={profile.ram_total_mb} />
              <Meter
                label="Disk"
                freeMb={profile.disk_free_mb}
                totalMb={profile.disk_total_mb}
                // Named rather than implied: the node aims at its models
                // directory and falls back to the volume anchor when that does
                // not exist yet, so "/" and "~/models" are different readings.
                caption={profile.disk_path}
              />
            </div>
          </>
        ) : (
          <p className="mt-4 rounded-lg border border-border p-3 text-sm text-text-muted">
            This node has never sent a profile. It reports one on its first beat, so either it has
            not heartbeat yet or it is running a build from before PRM-263.
          </p>
        )}

        {profile && profile.probe_errors.length > 0 && (
          <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-500/30 dark:bg-amber-500/10">
            <p className="text-xs font-medium text-amber-800 dark:text-amber-400">
              {profile.probe_errors.length === 1 ? "One probe failed" : "Some probes failed"} — the
              fields they would have filled read as unknown, not as absent.
            </p>
            <ul className="mt-1 space-y-0.5">
              {profile.probe_errors.map((error) => (
                <li key={error} className="font-mono text-xs text-amber-900 dark:text-amber-300">
                  {error}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* PRM-264. Below the hardware because it is the second half of the
            same question: what this machine is, and what it can actually start. */}
        <div className="mt-5">
          <div className="flex items-baseline justify-between gap-4">
            <h3 className="text-xs font-medium text-text-muted">Engines</h3>
            {engines !== null && (
              <span className="text-xs text-text-muted" title={node.engines_seen_at ?? undefined}>
                Scanned {timeAgo(node.engines_seen_at)}
              </span>
            )}
          </div>

          {tally && (
            <p className="mb-1 text-xs text-text-muted">
              <span className="text-text">{tally.installed} installed</span>
              {tally.installable > 0 && (
                <>
                  {" · "}
                  <span className="text-text">{tally.installable} could be added</span>
                </>
              )}
              {tally.ruledOut > 0 && ` · ${tally.ruledOut} ruled out by this hardware`}
            </p>
          )}

          {engines === null ? (
            <p className="mt-1 rounded-lg border border-border p-3 text-sm text-text-muted">
              This node has never scanned itself for engines.
              {node.engines !== null && node.engines.length > 0 && (
                <>
                  {" "}
                  It was registered as having {node.engines.map(engineLabel).join(", ")}, which is
                  what an operator said rather than what was found.
                </>
              )}
            </p>
          ) : (
            <ul className="mt-1 divide-y divide-border">
              {engines.map((engine) => (
                <EngineRow key={engine.backend} engine={engine} />
              ))}
            </ul>
          )}

          {/* The disagreement, named. This is the case PRM-264 exists for and
              the reason both lists are kept rather than one replacing the
              other — it is invisible if they are merged. */}
          {disagreement.length > 0 && (
            <p className="mt-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
              Registered as having {disagreement.map(engineLabel).join(", ")}, which this node did
              not find. Starting a model on {disagreement.length === 1 ? "it" : "them"} here would
              fail.
            </p>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
