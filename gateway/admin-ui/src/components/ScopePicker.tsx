import { useInstances } from "../api/instances";

// Mirrors auth-service's schemas.py VALID_SCOPES (fixed enum) — the only other
// valid scope shape is `model:<id>`, covered by the model picker below.
const FIXED_SCOPES = [
  "inference:read",
  "inference:stream",
  "admin:read",
  "admin:write",
  "admin:models",
  "admin:usage",
  "backend-registry:read",
  "backend-registry:write",
  "ui:chat",
  "ops:dashboard",
];

const MODEL_SCOPE_PREFIX = "model:";

export function ScopePicker({
  value,
  onChange,
}: {
  value: string[];
  onChange: (scopes: string[]) => void;
}) {
  const { data } = useInstances();
  const instances = data?.instances ?? [];

  // RM-24: model access is granted per model id, not per node — the same model
  // can be served by more than one node, so dedupe by id (first occurrence wins
  // for the displayed family/modality).
  const seenIds = new Set<string>();
  const modelOptions: { id: string; family: string; modality: string }[] = [];
  for (const entry of instances) {
    if (seenIds.has(entry.id)) continue;
    seenIds.add(entry.id);
    modelOptions.push({ id: entry.id, family: entry.family, modality: entry.modality });
  }

  // A previously-granted model:<id> scope whose model isn't in the current
  // discovery list (node down, model removed) still needs to render — otherwise
  // editing an existing user would silently drop their access to it on save.
  const knownModelScopes = new Set(modelOptions.map((m) => `${MODEL_SCOPE_PREFIX}${m.id}`));
  const staleModelScopes = value.filter(
    (s) => s.startsWith(MODEL_SCOPE_PREFIX) && !knownModelScopes.has(s),
  );

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

      {(modelOptions.length > 0 || staleModelScopes.length > 0) && (
        <div>
          <p className="mb-1 text-xs font-medium text-text-muted">Models</p>
          <div className="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
            {modelOptions.map((model) => {
              const scope = `${MODEL_SCOPE_PREFIX}${model.id}`;
              return (
                <label key={model.id} className="flex items-center gap-2 text-sm text-text">
                  <input type="checkbox" checked={value.includes(scope)} onChange={() => toggle(scope)} />
                  <span>{model.id}</span>
                  <span className="text-xs text-text-muted">
                    {model.family} · {model.modality}
                  </span>
                </label>
              );
            })}
            {staleModelScopes.map((scope) => (
              <label key={scope} className="flex items-center gap-2 text-sm text-text-muted">
                <input type="checkbox" checked onChange={() => toggle(scope)} />
                <span>{scope.slice(MODEL_SCOPE_PREFIX.length)}</span>
                <span className="text-xs italic">not currently found</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
