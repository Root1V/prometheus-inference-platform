import { Receipt, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useClientBillingSettings, useUpdateClientBillingSettings } from "../api/billing";
import { useToast } from "../context/ToastContext";
import { cn } from "../lib/cn";
import type { CurrencyCode } from "../lib/format";
import { getErrorMessage } from "../lib/errors";
import type { ClientBillingSettings } from "../types/billing";

interface ClientBillingSettingsModalProps {
  open: boolean;
  clientId: string;
  clientName: string;
  onClose: () => void;
}

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none";

const CURRENCIES: CurrencyCode[] = ["USD", "PEN", "EUR"];

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block text-sm text-text">
      <span className="mb-1 block text-xs font-medium text-text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-text-muted">{hint}</span>}
    </label>
  );
}

/** Mounted only once the current settings have loaded, mirroring
 * ModelSettingsModal's ModelSettingsForm — local state initializes straight
 * from `initial`, no effect needed to sync in server data after the fact. */
function ClientBillingSettingsForm({
  clientId,
  clientName,
  initial,
  onClose,
}: {
  clientId: string;
  clientName: string;
  initial: ClientBillingSettings;
  onClose: () => void;
}) {
  const { showToast } = useToast();
  const updateSettings = useUpdateClientBillingSettings();

  const [cap, setCap] = useState(initial.monthly_spend_cap_usd?.toString() ?? "");
  const [thresholds, setThresholds] = useState(initial.alert_thresholds_percent ?? "");
  const [taxRate, setTaxRate] = useState(initial.tax_rate_percent.toString());
  const [currency, setCurrency] = useState<CurrencyCode>(initial.preferred_currency);

  function handleSave() {
    const parsedCap = cap.trim() === "" ? null : Number(cap);
    if (parsedCap !== null && (Number.isNaN(parsedCap) || parsedCap < 0)) {
      showToast("Monthly spend cap must be a non-negative number, or blank for no cap", "error");
      return;
    }
    const parsedTax = Number(taxRate);
    if (Number.isNaN(parsedTax) || parsedTax < 0) {
      showToast("Tax rate must be a non-negative number", "error");
      return;
    }
    updateSettings.mutate(
      {
        clientId,
        data: {
          monthly_spend_cap_usd: parsedCap,
          alert_thresholds_percent: thresholds.trim() === "" ? null : thresholds.trim(),
          tax_rate_percent: parsedTax,
          preferred_currency: currency,
        },
      },
      {
        onSuccess: () => {
          showToast(`Billing settings updated for ${clientName}`, "success");
          onClose();
        },
        onError: (e) => showToast(getErrorMessage(e), "error"),
      },
    );
  }

  return (
    <>
      <div className="space-y-4">
        <Field
          label="Monthly spend cap (USD)"
          hint="Blank = no hard cap. Once reached, further requests get a 402 until next month."
        >
          <input
            value={cap}
            onChange={(e) => setCap(e.target.value)}
            placeholder="(no cap)"
            inputMode="decimal"
            className={inputClass}
          />
        </Field>
        <Field
          label="Alert thresholds (%)"
          hint="Comma-separated, e.g. 50,80,100. Blank = platform default. Notifies only — never blocks."
        >
          <input
            value={thresholds}
            onChange={(e) => setThresholds(e.target.value)}
            placeholder="(platform default)"
            className={inputClass}
          />
        </Field>
        <Field label="Tax rate (%)" hint="Shown as a separate line item on top of the subtotal.">
          <input
            value={taxRate}
            onChange={(e) => setTaxRate(e.target.value)}
            inputMode="decimal"
            className={inputClass}
          />
        </Field>
        <Field label="Preferred currency" hint="Display-only — cost is always computed in USD.">
          <select
            value={currency}
            onChange={(e) => setCurrency(e.target.value as CurrencyCode)}
            className={inputClass}
          >
            {CURRENCIES.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="mt-6 flex justify-end gap-3">
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-background"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={updateSettings.isPending}
          className={cn(
            "rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90",
            "disabled:cursor-not-allowed disabled:opacity-50",
          )}
        >
          {updateSettings.isPending ? "Saving…" : "Save"}
        </button>
      </div>
    </>
  );
}

export function ClientBillingSettingsModal({
  open,
  clientId,
  clientName,
  onClose,
}: ClientBillingSettingsModalProps) {
  const settingsQuery = useClientBillingSettings(clientId);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-8">
      <div className="w-full max-w-md rounded-xl bg-surface p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-text">
            <Receipt size={18} />
            Billing settings — {clientName}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={18} className="text-text-muted" />
          </button>
        </div>

        {settingsQuery.isLoading || !settingsQuery.data ? (
          <p className="text-sm text-text-muted">Loading…</p>
        ) : (
          <ClientBillingSettingsForm
            clientId={clientId}
            clientName={clientName}
            initial={settingsQuery.data}
            onClose={onClose}
          />
        )}
      </div>
    </div>,
    document.body,
  );
}
