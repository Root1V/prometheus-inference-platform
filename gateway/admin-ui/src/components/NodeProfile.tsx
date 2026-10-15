import { X } from "lucide-react";
import { createPortal } from "react-dom";
import { cn } from "../lib/cn";
import { engineDisagreement, engineLabel } from "../lib/engines";
import { timeAgo } from "../lib/format";
import type { DeviceProfile, Node } from "../types/node";

/**
 * What a node is, not just that it is up — PRM-270.
 *
 * PRM-263 made the agent read its own machine and send it with the heartbeat,
 * and the coordinator stored it. `/admin/api/nodes` has been returning it ever
 * since and this panel rendered none of it: the only way to see a node's
 * hardware was to open fleet.db by hand, which is the state PRM-206 already
 * fixed once for the heartbeat itself.
 *
 * Three things this deliberately does NOT do:
 *
 * - It never shows the profile without its age. The agent caches the reading
 *   for 5 minutes and a node that stopped reporting leaves its last one behind
 *   looking perfectly current.
 * - It never hides `probe_errors`. profile.py: *"an empty profile with no
 *   explanation is the shape that gets read as 'this machine has nothing'"* —
 *   a view that swallows them reintroduces exactly that.
 * - It says nothing about which models fit. That is PRM-265, and deriving it
 *   here would collapse a reading into a verdict, which is the separation
 *   PRM-263 was built to keep.
 */

function gb(mb: number | null): string {
  if (mb === null) return "unknown";
  return mb >= 1024 ? `${(mb / 1024).toFixed(mb >= 10_240 ? 0 : 1)} GB` : `${mb} MB`;
}

function cores(profile: DeviceProfile): string {
  const { cpu_cores_physical: physical, cpu_cores_logical: logical } = profile;
  if (physical === null && logical === null) return "unknown";
  if (physical !== null && logical !== null && physical !== logical) {
    return `${physical} cores · ${logical} threads`;
  }
  return `${physical ?? logical} cores`;
}

/** `kind` is the node's own answer, and "none" means it looked. */
function accelerator(profile: DeviceProfile): string {
  const accel = profile.accelerator;
  if (!accel) return "not probed";
  if (accel.kind === "none") return "none found";
  const parts = [accel.name ?? accel.kind];
  if (accel.memory_mb !== null) parts.push(gb(accel.memory_mb));
  if (accel.unified_memory) parts.push("unified memory");
  return parts.join(" · ");
}

function Row({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="shrink-0 text-xs font-medium text-text-muted">{label}</dt>
      <dd className="text-right text-sm text-text" title={hint}>
        {value}
      </dd>
    </div>
  );
}

export function NodeProfileModal({ node, onClose }: { node: Node; onClose: () => void }) {
  const profile = node.profile;
  const disagreement = engineDisagreement(node);

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-8">
      <div className="max-h-full w-full max-w-md overflow-y-auto rounded-xl bg-surface p-6 shadow-lg">
        <div className="mb-1 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text">{node.name}</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={18} className="text-text-muted" />
          </button>
        </div>

        {/* The age sits under the title, not next to a field, because it
            qualifies everything below it. */}
        <p className="mb-4 text-xs text-text-muted">
          {profile ? (
            <span title={node.profile_seen_at ?? undefined}>
              Reported {timeAgo(node.profile_seen_at)}
            </span>
          ) : (
            "No profile reported"
          )}
        </p>

        {profile ? (
          <dl className="divide-y divide-border">
            <Row label="OS" value={`${profile.os} ${profile.os_release}`} />
            <Row label="Architecture" value={profile.arch} />
            <Row label="CPU" value={profile.cpu_model ?? "unknown"} />
            <Row label="Cores" value={cores(profile)} />
            <Row
              label="Memory"
              value={`${gb(profile.ram_available_mb)} free of ${gb(profile.ram_total_mb)}`}
            />
            <Row
              label="Disk"
              value={`${gb(profile.disk_free_mb)} free of ${gb(profile.disk_total_mb)}`}
            />
            {/* Named, not implied: the node aims at its models directory and
                falls back to the volume anchor when that does not exist yet,
                so "/" and "~/models" are different readings worth telling
                apart before PRM-265 reasons about the number. */}
            <Row label="Measured at" value={profile.disk_path ?? "unknown"} />
            <Row label="Accelerator" value={accelerator(profile)} />
          </dl>
        ) : (
          <p className="rounded-lg border border-border p-3 text-sm text-text-muted">
            This node has never sent a profile. It reports one on its first beat, so either it has
            not heartbeat yet or it is running a build from before PRM-263.
          </p>
        )}

        {/* PRM-264. Deliberately below the hardware and above the probe
            errors, because it is the second half of the same question: what
            this machine is, and what it can actually start. */}
        <div className="mt-5">
          <div className="mb-1 flex items-baseline justify-between gap-4">
            <h3 className="text-xs font-medium text-text-muted">Engines</h3>
            {node.engines_detected !== null && (
              <span className="text-xs text-text-muted" title={node.engines_seen_at ?? undefined}>
                Scanned {timeAgo(node.engines_seen_at)}
              </span>
            )}
          </div>

          {node.engines_detected === null ? (
            <p className="rounded-lg border border-border p-3 text-sm text-text-muted">
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
            <ul className="space-y-1 rounded-lg border border-border p-2">
              {node.engines_detected.map((engine) => (
                <li key={engine.backend} className="flex items-baseline gap-2 text-sm">
                  <span
                    aria-hidden
                    className={cn(
                      "mt-1.5 size-1.5 shrink-0 rounded-full",
                      engine.installed ? "bg-green-500" : "bg-border",
                    )}
                  />
                  <span className={engine.installed ? "text-text" : "text-text-muted"}>
                    {engineLabel(engine.backend)}
                  </span>
                  {/* The resolved path for one that is present, the reason for
                      one that is not. Both answer "where would this come
                      from", which is the question after "is it here". */}
                  <span
                    className="ml-auto truncate text-right font-mono text-xs text-text-muted"
                    title={engine.installed ? engine.binary : (engine.reason ?? undefined)}
                  >
                    {engine.installed ? engine.path : engine.reason}
                  </span>
                </li>
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

        {/* Shown whenever there are any, including alongside a profile that
            otherwise looks complete — a probe that failed is why a field says
            "unknown", and without this the operator cannot tell "unknown"
            from "nothing there". */}
        {profile && profile.probe_errors.length > 0 && (
          <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-500/30 dark:bg-amber-500/10">
            <p className="mb-1 text-xs font-medium text-amber-800 dark:text-amber-400">
              {profile.probe_errors.length === 1 ? "One probe failed" : "Some probes failed"} — the
              fields they would have filled read as unknown, not as absent.
            </p>
            <ul className="space-y-0.5">
              {profile.probe_errors.map((error) => (
                <li key={error} className="font-mono text-xs text-amber-900 dark:text-amber-300">
                  {error}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
