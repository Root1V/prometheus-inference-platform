import { focusLink } from "./focus";

/**
 * What the platform can do, as something you can search for — PRM-247.
 *
 * The dashboard's search found clients and instances, which are the things
 * an operator already knows the name of. What it could not find is the
 * *settings*: rate-limit tiers live inside a page called Limits, currency
 * rates inside Billing, the login throttle inside a panel at the bottom of
 * Limits. Those are the ones worth searching for, because they are the ones
 * you cannot point at.
 *
 * `keywords` exist so the thing is reachable by the word the reader has, not
 * the word we chose: "quota" finds the tiers, "tax" finds the rates.
 */
export interface Feature {
  label: string;
  where: string;
  keywords: string;
  to: string;
}

export const FEATURES: Feature[] = [
  {
    label: "Rate-limit tiers",
    where: "Limits",
    keywords: "tier quota ceiling rpm tpm rpd ipm plan entitlement cupo tramo",
    to: focusLink("/limits", "rate-limits"),
  },
  {
    label: "The three limit layers",
    where: "Limits",
    keywords: "layer platform client endpoint ceiling capa limite",
    to: focusLink("/limits", "rate-limits"),
  },
  {
    label: "Which ceiling is refusing right now",
    where: "Limits",
    keywords: "429 refused saturation headroom live counters techo",
    to: focusLink("/limits", "right-now"),
  },
  {
    label: "Login throttle (per IP)",
    where: "Limits",
    keywords: "login brute force attempts ip throttle seguridad",
    to: focusLink("/limits", "login-throttle"),
  },
  {
    label: "Circuit breaker thresholds",
    where: "Limits",
    keywords: "circuit breaker failure recovery threshold backend",
    to: focusLink("/limits", "circuit-breaker"),
  },
  {
    label: "Model prices",
    where: "Billing",
    keywords: "price pricing cost per token usd precio tarifa",
    to: focusLink("/billing", "pricing"),
  },
  {
    label: "Currency rates",
    where: "Billing",
    keywords: "currency fx rate exchange pen eur moneda cambio",
    to: focusLink("/billing", "currency-rates"),
  },
  {
    label: "Spend caps and alerts",
    where: "Billing",
    keywords: "budget cap alert threshold spend limit presupuesto tope",
    to: focusLink("/billing", "alerts"),
  },
  {
    label: "Who is calling right now",
    where: "Activity",
    keywords: "live consumers sessions who activity ahora quien",
    to: focusLink("/activity", "here-now"),
  },
  {
    label: "End users behind a client",
    where: "Activity",
    keywords: "end user safety_identifier per user usuario final",
    to: focusLink("/activity", "end-users"),
  },
  {
    label: "Create a client or rotate a secret",
    where: "Users",
    keywords: "client credential secret scope grant token crear cliente",
    to: "/users",
  },
  {
    label: "Playground",
    where: "Playground",
    keywords: "try test prompt chat probar",
    to: "/playground",
  },
  {
    label: "Download a model",
    where: "Models",
    keywords: "download hugging face gguf pull descargar modelo",
    to: "/models",
  },
  {
    // The CSV button lives per row in the period history, so the anchor is
    // the section rather than the control — a row that may not exist yet is
    // not somewhere to land.
    label: "Usage export (CSV)",
    where: "Billing",
    keywords: "export csv download usage report factura descargar",
    to: focusLink("/billing", "export"),
  },
  {
    label: "Nodes",
    where: "Nodes",
    keywords: "node register host machine nodo",
    to: "/nodes",
  },
];

export function matchFeatures(needle: string): Feature[] {
  const q = needle.trim().toLowerCase();
  if (!q) return [];
  return FEATURES.filter((f) =>
    `${f.label} ${f.where} ${f.keywords}`.toLowerCase().includes(q),
  );
}
