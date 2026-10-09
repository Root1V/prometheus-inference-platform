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
    // A frame of delay: the element is usually rendered from data that has
    // not arrived when the route does, so querying immediately finds
    // nothing. One retry covers the fetch without a loop that could chase a
    // key that is never going to exist.
    let cancelled = false;
    const find = () => {
      if (cancelled) return;
      const element = document.querySelector<HTMLElement>(
        `[data-focus="${CSS.escape(key)}"]`,
      );
      if (!element) return;
      element.scrollIntoView({ behavior: "smooth", block: "center" });
      element.classList.add("focus-flash");
      window.setTimeout(() => element.classList.remove("focus-flash"), 3200);
    };
    const first = window.setTimeout(find, 60);
    const second = window.setTimeout(find, 900);
    return () => {
      cancelled = true;
      window.clearTimeout(first);
      window.clearTimeout(second);
    };
  }, [search]);
}
