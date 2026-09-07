import { Save } from "lucide-react";
import { useState } from "react";
import { useCurrencyRates, useUpdateCurrencyRates } from "../api/billing";
import type { CurrencyRates } from "../types/billing";
import { useToast } from "../context/ToastContext";
import { getErrorMessage } from "../lib/errors";

const inputClass =
  "w-24 rounded-lg border border-border bg-background px-2 py-1.5 text-sm text-text focus:border-primary focus:outline-none";

/**
 * RM-62 follow-up: the GET/PUT /admin/api/billing/currency-rates endpoints
 * (RM-60) had no consuming UI yet — the rates were only ever set by hand
 * against the DB. USD is the fixed base (always 1, not editable); PEN/EUR
 * are units-per-1-USD, e.g. 3.35 PEN or 0.86 EUR per USD — same numbers
 * Billing.tsx's currency toggle already converts with.
 *
 * Gated on the query loading first (rather than a useEffect syncing fetched
 * data into state) so the form only ever mounts once real values exist —
 * the inner component's useState then seeds correctly on its one render.
 */
export function CurrencyRatesForm() {
  const ratesQuery = useCurrencyRates();

  if (ratesQuery.isLoading || !ratesQuery.data) {
    return (
      <div className="rounded-xl border border-border bg-surface p-5 text-sm text-text-muted">
        Loading exchange rates…
      </div>
    );
  }

  return <CurrencyRatesFormLoaded rates={ratesQuery.data} />;
}

function CurrencyRatesFormLoaded({ rates }: { rates: CurrencyRates }) {
  const updateRates = useUpdateCurrencyRates();
  const { showToast } = useToast();

  const [pen, setPen] = useState(rates.PEN?.toString() ?? "");
  const [eur, setEur] = useState(rates.EUR?.toString() ?? "");

  function handleSave() {
    const penValue = Number(pen);
    const eurValue = Number(eur);
    if (pen.trim() === "" || Number.isNaN(penValue) || penValue <= 0) {
      showToast("PEN rate must be a positive number.", "error");
      return;
    }
    if (eur.trim() === "" || Number.isNaN(eurValue) || eurValue <= 0) {
      showToast("EUR rate must be a positive number.", "error");
      return;
    }
    updateRates.mutate(
      { PEN: penValue, EUR: eurValue },
      {
        onSuccess: () => showToast("Exchange rates saved", "success"),
        onError: (e) => showToast(getErrorMessage(e), "error"),
      },
    );
  }

  return (
    <div className="rounded-xl border border-border bg-surface p-5">
      <h2 className="text-sm font-medium uppercase tracking-wide text-text-muted">
        Exchange rates
      </h2>
      <p className="mt-1 text-xs text-text-muted">
        Units per 1 USD — display-only conversion for Billing's currency selector, updated by
        hand (no live FX feed).
      </p>
      <div className="mt-4 flex flex-wrap items-end gap-4">
        <label className="block text-sm text-text">
          <span className="mb-1 block text-xs font-medium text-text-muted">USD</span>
          <input value="1 (base)" disabled className={`${inputClass} opacity-60`} />
        </label>
        <label className="block text-sm text-text">
          <span className="mb-1 block text-xs font-medium text-text-muted">PEN per USD</span>
          <input
            value={pen}
            onChange={(e) => setPen(e.target.value)}
            inputMode="decimal"
            placeholder="3.35"
            className={inputClass}
          />
        </label>
        <label className="block text-sm text-text">
          <span className="mb-1 block text-xs font-medium text-text-muted">EUR per USD</span>
          <input
            value={eur}
            onChange={(e) => setEur(e.target.value)}
            inputMode="decimal"
            placeholder="0.86"
            className={inputClass}
          />
        </label>
        <button
          type="button"
          onClick={handleSave}
          disabled={updateRates.isPending}
          className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Save size={14} />
          {updateRates.isPending ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
