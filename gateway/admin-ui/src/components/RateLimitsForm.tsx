import { RotateCcw, Save } from "lucide-react";
import { useState } from "react";
import {
  useRateLimits,
  useResetRateLimits,
  useUpdateRateLimits,
  type LimitField,
  type LimitLayer,
  type RateLimitValues,
  type RateLimitsResponse,
} from "../api/limits";
import { useToast } from "../context/ToastContext";
import { getErrorMessage } from "../lib/errors";

/**
 * What each dimension bounds, in the fewest words that are still true.
 *
 * Keyed by the setting name rather than replacing it: the name is what goes
 * in `.env` and what an error message quotes, so the page shows both — the
 * name to copy, the sentence to understand it by.
 */
const FIELD_HINT: Record<string, string> = {
  rate_limit_rpm_platform: "requests/min, everyone combined",
  rate_limit_tpm_platform: "tokens/min, everyone combined",
  rate_limit_rpm_client: "requests/min per client, all endpoints",
  rate_limit_tpm_client: "tokens/min per client, all endpoints",
  rate_limit_tpm_input: "prompt tokens/min",
  rate_limit_tpm_output: "generated tokens/min",
  rate_limit_rpd: "requests/day",
  rate_limit_tpd: "tokens/day",
  rate_limit_ipm: "images/min, gated before generating",
  rate_limit_rpm: "requests/min per client, per endpoint",
  rate_limit_tpm: "tokens/min per client, per endpoint",
  rate_limit_rpm_chat_completions:
    "overrides the global for /v1/chat/completions",
  rate_limit_tpm_chat_completions:
    "overrides the global for /v1/chat/completions",
  rate_limit_rpm_predict: "overrides the global for /v1/models/{id}/predict",
  rate_limit_tpm_predict: "overrides the global for /v1/models/{id}/predict",
  rate_limit_rpm_admin: "this dashboard's own budget",
  rate_limit_tpm_admin: "this dashboard's own budget",
};

/** Blank is valid for everything except the two globals — it means the
 *  dimension's own default, which the placeholder names. */
function parseLimit(raw: string): number | null | "invalid" {
  if (raw.trim() === "") return null;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1) return "invalid";
  return n;
}

/**
 * What a blank field will fall back to — PRM-232.
 *
 * The distinction this page kept getting wrong: unset is not zero and not
 * "nothing happens". A blank platform ceiling refuses nothing; a blank
 * `rpm_client` is enforced at the sum of the per-endpoint allowances; a blank
 * per-endpoint override uses the global. Three different meanings for an empty
 * box, so the box says which one it has.
 */
function placeholderFor(field: LimitField, layer: LimitLayer["layer"]): string {
  if (field.source === "derived" && field.value !== null) {
    // The number alone. "(computed)" was in here and the box cut it to
    // "240,000 (com" — a hint that needs the whole box is not a hint, so the
    // word moved to the line underneath where there is room for it.
    return field.value.toLocaleString();
  }
  if (
    layer === "endpoint" &&
    !["rate_limit_rpm", "rate_limit_tpm"].includes(field.field)
  ) {
    return "use global";
  }
  return "no ceiling";
}

function LimitInput({
  field,
  layer,
  value,
  envDefault,
  onChange,
}: {
  field: LimitField;
  layer: LimitLayer["layer"];
  value: string;
  envDefault: number | null;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="truncate font-mono text-xs text-text" title={field.field}>
          {field.field.replace("rate_limit_", "")}
        </p>
        <p className="mt-0.5 text-[11px] leading-tight text-text-muted">
          {FIELD_HINT[field.field]}
        </p>
      </div>
      <div className="shrink-0 text-right">
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          inputMode="numeric"
          placeholder={placeholderFor(field, layer)}
          aria-label={field.field}
          className="w-28 rounded-lg border border-border bg-background px-2 py-1 text-right text-sm tabular-nums text-text focus:border-primary focus:outline-none"
        />
        {field.source === "derived" ? (
          <p
            className="mt-0.5 text-[10px] text-primary"
            title="Nobody set this — it is the sum of the per-endpoint allowances, computed and enforced. Type a number to pin it, or leave it blank to keep following the global."
          >
            computed, follows the global
          </p>
        ) : (
          <p className="mt-0.5 text-[10px] text-text-muted">
            .env: {envDefault === null ? "unset" : envDefault.toLocaleString()}
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * One layer, editable in place — PRM-232.
 *
 * PRM-229 drew these three cards read-only beside a form that edited six of
 * the numbers on them. With all seventeen editable that would have put every
 * value on the page twice, and the second copy is the one that goes stale.
 * The card that explains what a layer bounds is the right place to change it.
 */
function LayerCard({
  layer,
  values,
  envDefaults,
  onChange,
}: {
  layer: LimitLayer;
  values: Record<string, string>;
  envDefaults: RateLimitValues;
  onChange: (field: string, v: string) => void;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
        {layer.layer}
      </p>
      <p className="mt-1 text-sm text-text">{layer.what}</p>
      <div className="mt-3 space-y-2.5">
        {layer.fields.map((f) => (
          <LimitInput
            key={f.field}
            field={f}
            layer={layer.layer}
            value={values[f.field] ?? ""}
            envDefault={
              (envDefaults as unknown as Record<string, number | null>)[
                f.field
              ] ?? null
            }
            onChange={(v) => onChange(f.field, v)}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * RM-56: rate limits used to be .env-only, needing a gateway restart to
 * change. The gateway re-reads them per request, so a save here applies to
 * the very next request; the DB row behind it is what survives a restart.
 */
export function RateLimitsForm() {
  const limitsQuery = useRateLimits();

  if (limitsQuery.isLoading || !limitsQuery.data) {
    return (
      <div className="rounded-xl border border-border bg-surface p-5 text-sm text-text-muted">
        Loading rate limits…
      </div>
    );
  }

  return (
    <RateLimitsFormLoaded
      key={String(limitsQuery.data.is_overridden)}
      data={limitsQuery.data}
    />
  );
}

function RateLimitsFormLoaded({ data }: { data: RateLimitsResponse }) {
  const update = useUpdateRateLimits();
  const reset = useResetRateLimits();
  const { showToast } = useToast();

  // Only what someone actually chose starts in the box. A `derived` value
  // prefilled would be pinned by the first save that touched any other field
  // — the consumer ceiling would stop following the global it is computed
  // from, silently, and nothing on the page would say so.
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      data.layers.flatMap((layer) =>
        layer.fields.map((f) => [
          f.field,
          f.source === "set" && f.value !== null ? String(f.value) : "",
        ]),
      ),
    ),
  );

  const isBusy = update.isPending || reset.isPending;
  const setField = (field: string, v: string) =>
    setValues((prev) => ({ ...prev, [field]: v }));

  function handleSave() {
    const parsed: Record<string, number | null> = {};
    for (const [field, raw] of Object.entries(values)) {
      const v = parseLimit(raw);
      if (v === "invalid") {
        showToast(
          "Limits must be whole numbers of 1 or more, or blank.",
          "error",
        );
        return;
      }
      parsed[field] = v;
    }
    if (parsed.rate_limit_rpm === null || parsed.rate_limit_tpm === null) {
      showToast("The global RPM and TPM limits are required.", "error");
      return;
    }
    update.mutate(parsed as unknown as RateLimitValues, {
      onSuccess: () =>
        showToast("Rate limits saved — applied to the next request", "success"),
      onError: (e) => showToast(getErrorMessage(e), "error"),
    });
  }

  function handleReset() {
    reset.mutate(undefined, {
      onSuccess: () => showToast("Reverted to the values from .env", "success"),
      onError: (e) => showToast(getErrorMessage(e), "error"),
    });
  }

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-3xl text-sm text-text-muted">
          A request passes every layer that applies, not whichever is most
          specific. The narrowest ceiling it meets is the one that stops it.
          Changes apply to the very next request — no restart — and{" "}
          <span className="text-text">
            saving makes this page the source for all of them
          </span>
          ; Reset puts every dimension back to what .env configured. The admin
          API can&rsquo;t go below {data.min_admin_rpm} RPM, so a mistake here
          can never lock you out of this page.
        </p>
        <div className="flex shrink-0 items-center gap-2">
          <span
            className={
              "rounded-full px-2 py-0.5 text-xs " +
              (data.is_overridden
                ? "bg-primary/10 text-primary"
                : "bg-background text-text-muted")
            }
          >
            {data.is_overridden ? "custom" : "from .env"}
          </span>
          <button
            type="button"
            onClick={handleSave}
            disabled={isBusy}
            className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Save size={14} />
            {update.isPending ? "Saving…" : "Save"}
          </button>
          {data.is_overridden && (
            <button
              type="button"
              onClick={handleReset}
              disabled={isBusy}
              title="Discard the saved limits and go back to what .env configured"
              className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:bg-background disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RotateCcw size={14} />
              Reset to .env
            </button>
          )}
        </div>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {data.layers.map((layer) => (
          <LayerCard
            key={layer.layer}
            layer={layer}
            values={values}
            envDefaults={data.env_defaults}
            onChange={setField}
          />
        ))}
      </div>

      {data.login_throttle && (
        <div className="mt-4 rounded-xl border border-border bg-surface px-4 py-3">
          {/* PRM-233: the ceiling this page never showed. Every limit above is
              keyed on a credential and therefore applies after authentication;
              this is the only one that applies to someone who does not have
              one yet. Read-only, and deliberately: a login throttle an
              attacker could widen through the admin API is not obviously one
              worth making editable through the admin API. */}
          <p className="flex flex-wrap items-baseline gap-x-2 text-sm">
            <span className="font-medium text-text">
              Login attempts: {data.login_throttle.rpm} per minute, per source
              IP
            </span>
            <span className="text-text-muted">
              {data.login_throttle.active
                ? "The only ceiling here that applies before a credential is accepted — everything above is counted per client."
                : "Inactive: the /ui/* surface is disabled on this deployment, so nothing reaches this check."}
            </span>
            <span className="text-xs text-text-muted">(.env only)</span>
          </p>
        </div>
      )}

      {data.capacity?.reporting > 0 && (
        <p className="mt-3 max-w-3xl text-xs text-text-muted">
          {/* RM-71 decision #8: observed capacity beside the limit, never used
              to derive one. */}
          Right now the running backends report{" "}
          <span className="font-medium text-text">
            {data.capacity.slots} concurrent slots
          </span>{" "}
          across {data.capacity.reporting} instance
          {data.capacity.reporting === 1 ? "" : "s"} — what the hardware can
          genuinely work on at once. It isn&rsquo;t a suggested limit: how many
          requests a slot absorbs depends on the model and the prompt.
          It&rsquo;s here so the numbers above aren&rsquo;t set blind.
        </p>
      )}
    </>
  );
}
