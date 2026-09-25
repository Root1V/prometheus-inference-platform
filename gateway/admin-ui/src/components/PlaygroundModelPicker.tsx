import type { InstanceEntry, Modality } from "../types/instance";

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none";

/** PRM-137: the groups, and the order they appear in.
 *
 * This used to be three hard-coded `.filter()` calls — Text & Vision,
 * Embedding, Image — which meant every modality added since was silently
 * dropped from the picker. `rerank` had been missing since PRM-106 and nobody
 * noticed, because a model that is absent from a dropdown does not look like a
 * bug, it looks like a model nobody started.
 *
 * Anything not named here still appears, under its own raw modality: the
 * fallback is "show it with an ugly label", never "hide it".
 */
const GROUP_LABELS: Partial<Record<Modality, string>> = {
  text: "Text & Vision",
  vision: "Text & Vision",
  embedding: "Embedding",
  rerank: "Rerank",
  classification: "Classification",
  zero_shot: "Decision (zero-shot)",
  typed_decision: "Decision (typed questions)",
  image: "Image",
};

const GROUP_ORDER = [
  "Text & Vision",
  "Embedding",
  "Rerank",
  "Classification",
  "Decision (zero-shot)",
  "Decision (typed questions)",
  "Image",
];

/** RM-53: one Model selector spanning every modality, replacing three
 * separately-filtered <select> blocks (Chat/Embeddings/Images) — grouped by
 * modality (via <optgroup>) so the combined list stays scannable. Dumb
 * component: any side effect of switching models (e.g. clearing an attached
 * image that's no longer sendable) is the caller's `onChange` handler's job,
 * not this component's. */
export function PlaygroundModelPicker({
  instances,
  value,
  onChange,
}: {
  instances: InstanceEntry[];
  value: string;
  onChange: (id: string) => void;
}) {
  if (instances.length === 0) {
    return (
      <p className="text-sm text-text-muted">
        No running models — start one from Instances first.
      </p>
    );
  }

  const grouped = new Map<string, InstanceEntry[]>();
  for (const instance of instances) {
    const label = GROUP_LABELS[instance.modality] ?? instance.modality;
    const bucket = grouped.get(label);
    if (bucket) bucket.push(instance);
    else grouped.set(label, [instance]);
  }
  const groups = [...grouped.entries()].sort(
    (a, b) =>
      (GROUP_ORDER.indexOf(a[0]) + 1 || GROUP_ORDER.length + 1) -
      (GROUP_ORDER.indexOf(b[0]) + 1 || GROUP_ORDER.length + 1),
  );

  return (
    <select
      id="playground-model"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={inputClass}
    >
      {groups.map(([label, members]) => (
        <optgroup key={label} label={label}>
          {members.map((i) => (
            <option key={i.id} value={i.id}>
              {i.id}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}
