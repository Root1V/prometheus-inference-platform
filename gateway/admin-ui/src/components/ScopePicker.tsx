import { useInstances, useNodes } from "../api/instances";

// Mirrors auth-service's schemas.py VALID_SCOPES (fixed enum). The other two
// valid shapes are patterns rather than members of that set, and each has its
// own picker below: `model:<id>` and `node:<id>`.
//
// PRM-267: the mirror is checked by gateway/tests/test_ui_scopes.py. It used to
// be only claimed here, and `fleet:heartbeat` was missing — so the one scope a
// node credential needs could not be granted from the panel that is the only
// place credentials are created.
const FIXED_SCOPES = [
  "inference:read",
  "inference:stream",
  "admin:read",
  "admin:write",
  "admin:models",
  "admin:usage",
  "backend-registry:read",
  "backend-registry:write",
  "fleet:heartbeat",
  "ui:chat",
];

const MODEL_SCOPE_PREFIX = "model:";
const NODE_SCOPE_PREFIX = "node:";

// Only these scopes mean the principal actually calls a model (API inference or
// the chat UI) — admin/internal-tooling users (admin:*, backend-registry:*)
// never need per-model access, so the picker stays hidden for them.
const MODEL_CONSUMER_SCOPES = ["inference:read", "inference:stream", "ui:chat"];

// `fleet:heartbeat` says a credential may report liveness; `node:<id>` says
// which node it may report it *for*. The pair is the whole point (schemas.py:
// "a compromised node can forge liveness for itself and for nothing else"), so
// checking one has to offer the other.
const FLEET_SCOPE = "fleet:heartbeat";

type ModelOption = {
  slug: string;
  family: string;
  modality: string;
  running: number;
  total: number;
};

export function ScopePicker({
  value,
  onChange,
}: {
  value: string[];
  onChange: (scopes: string[]) => void;
}) {
  const { data } = useInstances();
  const instances = data?.instances ?? [];
  const { data: nodes } = useNodes();
  const nodeOptions = [...(nodes ?? [])].sort((a, b) => a.name.localeCompare(b.name));

  // RM-70: access is granted to a *model*, never to an instance. Replicas used
  // to appear one row each, suffixes and all, so granting access meant picking
  // a specific process — which pinned that client to one replica and skipped
  // the load balancing entirely. One row per catalog slug instead, with how
  // many instances are actually serving it.
  const bySlug = new Map<string, ModelOption>();
  for (const entry of instances) {
    const slug = entry.model_slug || entry.model_id || entry.id;
    const option = bySlug.get(slug) ?? {
      slug,
      family: entry.family,
      modality: entry.modality,
      running: 0,
      total: 0,
    };
    option.total += 1;
    if (entry.state === "ready") option.running += 1;
    bySlug.set(slug, option);
  }
  const modelOptions = [...bySlug.values()].sort((a, b) => a.slug.localeCompare(b.slug));

  // A granted scope that isn't one of the models above still has to render, or
  // editing a user would silently drop it on save. Two different reasons it can
  // happen, and they read differently: a grant naming an instance is a legacy
  // pin from before models became the unit, while anything else is a model
  // that simply isn't here right now (node down, model removed).
  const instanceIds = new Set(instances.map((i) => i.id));
  const knownModelScopes = new Set(modelOptions.map((m) => `${MODEL_SCOPE_PREFIX}${m.slug}`));
  const otherModelScopes = value
    .filter((s) => s.startsWith(MODEL_SCOPE_PREFIX) && !knownModelScopes.has(s))
    .map((scope) => {
      const name = scope.slice(MODEL_SCOPE_PREFIX.length);
      return { scope, name, legacyInstance: instanceIds.has(name) };
    });

  // Same reason, for nodes: a grant for a node this coordinator no longer lists
  // (deregistered, or registered elsewhere) must stay visible and keepable.
  const knownNodeScopes = new Set(nodeOptions.map((n) => `${NODE_SCOPE_PREFIX}${n.id}`));
  const otherNodeScopes = value
    .filter((s) => s.startsWith(NODE_SCOPE_PREFIX) && !knownNodeScopes.has(s))
    .map((scope) => ({ scope, id: scope.slice(NODE_SCOPE_PREFIX.length) }));

  // Show the picker once the user is (or already was) a model consumer — either
  // a consumer scope is checked now, or they already hold model:<id> grants from
  // before (so editing never hides — and risks silently dropping — existing access).
  const hasModelGrants = value.some((s) => s.startsWith(MODEL_SCOPE_PREFIX));
  const isModelConsumer = MODEL_CONSUMER_SCOPES.some((s) => value.includes(s)) || hasModelGrants;

  const hasNodeGrants = value.some((s) => s.startsWith(NODE_SCOPE_PREFIX));
  const isFleetMember = value.includes(FLEET_SCOPE) || hasNodeGrants;

  const toggle = (scope: string) => {
    onChange(value.includes(scope) ? value.filter((s) => s !== scope) : [...value, scope]);
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-1.5">
        {FIXED_SCOPES.map((scope) => (
          <label key={scope} className="flex items-center gap-2 text-sm text-text">
            <input type="checkbox" checked={value.includes(scope)} onChange={() => toggle(scope)} />
            {scope}
          </label>
        ))}
      </div>

      {isFleetMember && (
        <div>
          <p className="mb-1 text-xs font-medium text-text-muted">Nodes</p>
          <div className="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
            {nodeOptions.map((node) => {
              const scope = `${NODE_SCOPE_PREFIX}${node.id}`;
              return (
                <label key={node.id} className="flex items-center gap-2 text-sm text-text">
                  <input
                    type="checkbox"
                    checked={value.includes(scope)}
                    onChange={() => toggle(scope)}
                  />
                  <span className="truncate">{node.name}</span>
                  <span className="shrink-0 text-xs text-text-muted">
                    {node.node_type}
                    {node.tag ? ` · ${node.tag}` : ""}
                  </span>
                  <span
                    title="The node id this credential speaks for — also PMGR_FLEET_NODE_ID on the device"
                    className="ml-auto shrink-0 font-mono text-xs text-text-muted"
                  >
                    {node.id}
                  </span>
                </label>
              );
            })}
            {otherNodeScopes.map(({ scope, id }) => (
              <label key={scope} className="flex items-center gap-2 text-sm text-text-muted">
                <input type="checkbox" checked onChange={() => toggle(scope)} />
                <span className="truncate font-mono text-xs">{id}</span>
                <span className="shrink-0 text-xs italic">not in this fleet</span>
              </label>
            ))}
            {nodeOptions.length === 0 && otherNodeScopes.length === 0 && (
              <p className="text-xs italic text-text-muted">
                No nodes registered — register the node first, then grant it here.
              </p>
            )}
          </div>
        </div>
      )}

      {isModelConsumer && (modelOptions.length > 0 || otherModelScopes.length > 0) && (
        <div>
          <p className="mb-1 text-xs font-medium text-text-muted">Models</p>
          <div className="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
            {modelOptions.map((model) => {
              const scope = `${MODEL_SCOPE_PREFIX}${model.slug}`;
              return (
                <label key={model.slug} className="flex items-center gap-2 text-sm text-text">
                  <input
                    type="checkbox"
                    checked={value.includes(scope)}
                    onChange={() => toggle(scope)}
                  />
                  <span>{model.slug}</span>
                  <span className="text-xs text-text-muted">
                    {model.family} · {model.modality}
                  </span>
                  <span
                    title={
                      model.running === model.total
                        ? "Instances currently serving this model"
                        : `${model.running} of ${model.total} instances are running`
                    }
                    className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary"
                  >
                    {model.running} running
                  </span>
                </label>
              );
            })}
            {otherModelScopes.map(({ scope, name, legacyInstance }) => (
              <label key={scope} className="flex items-center gap-2 text-sm text-text-muted">
                <input type="checkbox" checked onChange={() => toggle(scope)} />
                <span>{name}</span>
                <span className="text-xs italic">
                  {legacyInstance
                    ? "pinned to one instance — reissue as a model grant"
                    : "not currently found"}
                </span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
