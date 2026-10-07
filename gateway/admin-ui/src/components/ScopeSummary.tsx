/**
 * Platform scopes, summarised so the exception is what you see.
 *
 * PRM-201. The first pass collapsed these to "2 scopes", which hid the one
 * thing worth knowing. Showing them all hides it too, and for the same reason:
 * measured on this deployment, **eleven of fourteen credentials carry exactly
 * `inference:read` + `inference:stream`**. Printed in full, that is eleven rows
 * of identical text, and the three that differ — the registry writer, the two
 * fleet nodes, and the one credential holding `admin:write` — disappear into it.
 *
 * So each scope family becomes one chip carrying its verbs, and the families
 * that confer privilege are accented. A scan down the column is then a wall of
 * muted `inference r·s` with the exceptions standing out, which is the question
 * this column exists to answer: who can do more than call a model.
 *
 * The full scope strings stay available on hover rather than being thrown
 * away — abbreviation is for scanning, and the exact value is what someone
 * copies into a grant.
 */

const VERB_ABBREVIATIONS: Record<string, string> = {
  read: "r",
  write: "w",
  stream: "s",
  heartbeat: "hb",
};

/**
 * Families that let a credential change the platform rather than use it.
 * `backend-registry:write` is here and `backend-registry:read` is not —
 * reading the catalogue is what every caller does; writing it starts and stops
 * models.
 */
function isPrivileged(family: string, verbs: string[]): boolean {
  if (family === "admin") return true;
  if (family === "backend-registry") return verbs.includes("write");
  return false;
}

type ScopeGroup = {
  family: string;
  verbs: string[];
  full: string[];
  privileged: boolean;
};

function groupScopes(scopes: string[]): ScopeGroup[] {
  const byFamily = new Map<string, { verbs: string[]; full: string[] }>();
  for (const scope of scopes) {
    const [family, verb] = scope.includes(":") ? scope.split(":", 2) : [scope, ""];
    const entry = byFamily.get(family) ?? { verbs: [], full: [] };
    // A `node:<uuid>` scope names an identity, not an action — the uuid is not
    // a verb and abbreviating it would produce noise like `node c`.
    if (verb && family !== "node") entry.verbs.push(verb);
    entry.full.push(scope);
    byFamily.set(family, entry);
  }
  return [...byFamily.entries()]
    .map(([family, { verbs, full }]) => ({
      family,
      verbs,
      full,
      privileged: isPrivileged(family, verbs),
    }))
    // Privileged first: the reason to look at this column is at the top of it.
    .sort((a, b) => Number(b.privileged) - Number(a.privileged) || a.family.localeCompare(b.family));
}

export function ScopeSummary({ scopes }: { scopes: string[] }) {
  const groups = groupScopes(scopes);
  if (groups.length === 0) {
    return <span className="text-xs text-text-muted">—</span>;
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      {groups.map(({ family, verbs, full, privileged }) => (
        <span
          key={family}
          title={full.join("\n")}
          className={
            privileged
              ? "inline-flex items-center gap-1 rounded-full border border-amber-400/50 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
              : "inline-flex items-center gap-1 rounded-full bg-background px-2 py-0.5 text-xs text-text-muted"
          }
        >
          {family}
          {verbs.length > 0 && (
            <span className={privileged ? "text-amber-700 dark:text-amber-400/80" : "opacity-60"}>
              {verbs.map((v) => VERB_ABBREVIATIONS[v] ?? v).join("·")}
            </span>
          )}
        </span>
      ))}
    </div>
  );
}
