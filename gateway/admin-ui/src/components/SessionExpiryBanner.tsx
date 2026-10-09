import { useEffect, useState } from "react";
import { Clock, X } from "lucide-react";
import { useAuth } from "../context/AuthContext";

/** How long before the deadline the warning appears. */
const WARN_WITHIN_MS = 5 * 60 * 1000;

/**
 * PRM-249: the last five minutes, said out loud.
 *
 * The token lasts three hours and nothing announced the end of it, so the
 * operator met the deadline as a page full of failed panels — usually while
 * part-way through a form. This is deliberately not a toast: the existing ones
 * clear themselves after six seconds, and a warning nobody is looking at when
 * it appears is not a warning. It is also deliberately not a countdown — the
 * useful fact is the wall-clock time the session ends, which does not change,
 * so there is nothing to tick.
 *
 * There is no refresh-token flow to offer, so the banner does not pretend the
 * session can be extended. It buys the one thing it can: time to finish a
 * sentence and submit it.
 */
export function SessionExpiryBanner() {
  const { expiresAt } = useAuth();
  // Both pieces of state name the deadline they belong to rather than being a
  // bare boolean reset on change. Signing out and back in gives a new
  // deadline, and a plain `true` would have been inherited by the new session
  // — the banner would appear at once, three hours early.
  const [warnedFor, setWarnedFor] = useState<number | null>(null);
  const [dismissedFor, setDismissedFor] = useState<number | null>(null);

  useEffect(() => {
    if (expiresAt === null) return;
    // Fired from a timer rather than computed while rendering, so the clock is
    // never read during a render pass.
    const timer = window.setTimeout(
      () => setWarnedFor(expiresAt),
      Math.max(0, expiresAt - WARN_WITHIN_MS - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [expiresAt]);

  if (
    expiresAt === null ||
    warnedFor !== expiresAt ||
    dismissedFor === expiresAt
  ) {
    return null;
  }

  const endsAt = new Date(expiresAt).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <div className="fixed bottom-4 left-1/2 z-[100] flex -translate-x-1/2 items-center gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-2.5 text-sm text-amber-900 shadow-lg dark:border-amber-800/60 dark:bg-amber-950/60 dark:text-amber-100">
      <Clock size={15} className="shrink-0" aria-hidden />
      <span>
        Your session ends at {endsAt}. Finish and submit what you are working
        on — you will need to sign in again.
      </span>
      <button
        type="button"
        onClick={() => setDismissedFor(expiresAt)}
        aria-label="Dismiss session warning"
        className="shrink-0 rounded-md p-1 transition-colors hover:bg-amber-100 dark:hover:bg-amber-900/60"
      >
        <X size={14} />
      </button>
    </div>
  );
}
