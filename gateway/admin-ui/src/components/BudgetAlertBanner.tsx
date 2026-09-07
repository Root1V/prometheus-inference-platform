import { AlertTriangle, X } from "lucide-react";
import { useState } from "react";
import { useBillingAlerts } from "../api/billing";
import { useUsers } from "../api/users";
import { formatUsdCost } from "../lib/format";

/**
 * RM-60: a sibling of WarningBanner (same dismiss/visual pattern) rather than
 * a generalization of it — WarningBanner is hardcoded to `nodes: string[]`
 * for Dashboard.tsx's unreachable-nodes case, an unrelated caller. This one
 * polls useBillingAlerts() for clients that have crossed a soft alert
 * threshold this period — notify only, never blocks (the hard cap is
 * enforced server-side regardless of whether this banner is even seen).
 */
export function BudgetAlertBanner() {
  const [dismissed, setDismissed] = useState(false);
  const alertsQuery = useBillingAlerts();
  const usersQuery = useUsers();

  const alerts = alertsQuery.data?.data ?? [];
  if (dismissed || alerts.length === 0) return null;

  const users = usersQuery.data ?? [];
  const nameByClientId = new Map(users.map((u) => [u.client_id, u.client_name]));

  return (
    <div className="flex items-start justify-between gap-4 rounded-lg border border-yellow-200 bg-yellow-50 px-4 py-3 text-sm text-yellow-800 dark:border-yellow-900 dark:bg-yellow-500/10 dark:text-yellow-300">
      <div className="flex items-start gap-2">
        <AlertTriangle size={18} className="mt-0.5 shrink-0" />
        <span>
          Approaching or over their monthly spend cap:{" "}
          {alerts.map((alert, i) => (
            <span key={alert.client_id}>
              {i > 0 && ", "}
              <strong>{nameByClientId.get(alert.client_id) ?? alert.client_id}</strong> (
              {formatUsdCost(alert.spend_usd)} of{" "}
              {alert.monthly_spend_cap_usd !== null
                ? formatUsdCost(alert.monthly_spend_cap_usd)
                : "—"}
              {" · "}
              {alert.crossed_thresholds_percent[alert.crossed_thresholds_percent.length - 1]}%+)
            </span>
          ))}
        </span>
      </div>
      <button type="button" onClick={() => setDismissed(true)} aria-label="Dismiss warning">
        <X size={16} />
      </button>
    </div>
  );
}
