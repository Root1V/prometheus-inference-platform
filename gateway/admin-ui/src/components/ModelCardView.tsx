import { useModelCard } from "../api/models";
import { getErrorMessage } from "../lib/errors";

/** Renders a Hugging Face model card's raw markdown text — shared between
 * the Discover tab's "show model card" toggle and the Library preview
 * panel, so both stay in sync with a single fetch/render implementation.
 * No markdown-rendering dependency added — this is deliberately a plain
 * `<pre>` of the raw text, matching the rest of the Models page's "keep it
 * simple, this is an operator tool" scope. */
export function ModelCardView({ node, repoId }: { node: string; repoId: string }) {
  const cardQuery = useModelCard(node, repoId);

  if (cardQuery.isLoading) {
    return <p className="text-sm text-text-muted">Loading model card…</p>;
  }
  if (cardQuery.isError) {
    return <p className="text-sm text-red-600">{getErrorMessage(cardQuery.error)}</p>;
  }
  if (!cardQuery.data) return null;

  return (
    <pre className="whitespace-pre-wrap break-words font-sans text-xs text-text">
      {cardQuery.data.text}
    </pre>
  );
}
