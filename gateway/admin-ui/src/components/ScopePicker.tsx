import { useState } from "react";

// Mirrors auth-service's schemas.py VALID_SCOPES (fixed enum). model:<id> scopes
// are open-ended (one per registered model) so they're entered as free text
// instead of listed here — there's no "list scopes" backend endpoint, and
// adding one just to render a checkbox list would be unnecessary scope creep.
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

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none";

export function ScopePicker({
  value,
  onChange,
}: {
  value: string[];
  onChange: (scopes: string[]) => void;
}) {
  const [customScope, setCustomScope] = useState("");
  const customScopes = value.filter((s) => !FIXED_SCOPES.includes(s));

  const toggle = (scope: string) => {
    onChange(value.includes(scope) ? value.filter((s) => s !== scope) : [...value, scope]);
  };

  const addCustom = () => {
    const trimmed = customScope.trim();
    if (trimmed && !value.includes(trimmed)) {
      onChange([...value, trimmed]);
    }
    setCustomScope("");
  };

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-1.5">
        {FIXED_SCOPES.map((scope) => (
          <label key={scope} className="flex items-center gap-2 text-sm text-text">
            <input type="checkbox" checked={value.includes(scope)} onChange={() => toggle(scope)} />
            {scope}
          </label>
        ))}
      </div>
      {customScopes.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {customScopes.map((scope) => (
            <span
              key={scope}
              className="inline-flex items-center gap-1 rounded-full bg-background px-2.5 py-0.5 text-xs text-text-muted"
            >
              {scope}
              <button
                type="button"
                aria-label={`Remove ${scope}`}
                onClick={() => onChange(value.filter((s) => s !== scope))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <input
          value={customScope}
          onChange={(e) => setCustomScope(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addCustom();
            }
          }}
          placeholder="model:llama3-8b-q4-local"
          className={inputClass}
        />
        <button
          type="button"
          onClick={addCustom}
          className="shrink-0 rounded-lg border border-border px-3 py-2 text-sm font-medium text-text hover:bg-background"
        >
          Add
        </button>
      </div>
    </div>
  );
}
