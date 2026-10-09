import { useEffect } from "react";
import { useLocation } from "react-router-dom";

/**
 * Take the reader to the thing they searched for — PRM-247.
 *
 * The search can reach a setting that lives two scrolls down a page of
 * seventeen inputs, and landing on that page is not the same as finding it.
 * A link carries `?focus=<key>`; the destination scrolls the element marked
 * `data-focus="<key>"` into view and rings it for three seconds.
 *
 * Marked, not scrolled-and-forgotten: on a page where every card looks alike,
 * "it is somewhere above" is the part that costs the time.
 */
export const FOCUS_PARAM = "focus";

/** Build a link that lands on `key` within `route`. */
export function focusLink(route: string, key?: string): string {
  return key ? `${route}?${FOCUS_PARAM}=${encodeURIComponent(key)}` : route;
}

/** Call once per page that has `data-focus` targets. */
export function useFocusFlash(): void {
  const { search } = useLocation();
  useEffect(() => {
    const key = new URLSearchParams(search).get(FOCUS_PARAM);
    if (!key) return;
    // Polled to a deadline, not tried twice.
    //
    // The element is rendered from data the route does not wait for, so
    // querying on arrival finds nothing. Two fixed retries (60ms, 900ms)
    // covered a page that answers quickly and missed Models entirely, which
    // takes over three seconds to list a node's catalogue — by the time the
    // row existed the window had closed and the highlight never ran. Every
    // 250ms for eight seconds, stopping the moment it appears, so a slow
    // page still lands and a key that will never match costs eight seconds
    // of a timer and nothing else.
    let cancelled = false;
    const deadline = Date.now() + 8000;
    let timer = 0;

    const find = () => {
      if (cancelled) return;
      const element = document.querySelector<HTMLElement>(
        `[data-focus="${CSS.escape(key)}"]`,
      );
      if (element) {
        element.scrollIntoView({ behavior: "smooth", block: "center" });
        element.classList.add("focus-flash");
        window.setTimeout(() => element.classList.remove("focus-flash"), 3200);
        return;
      }
      if (Date.now() < deadline) timer = window.setTimeout(find, 250);
    };
    find();

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [search]);
}
