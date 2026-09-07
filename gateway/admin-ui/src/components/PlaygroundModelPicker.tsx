import type { InstanceEntry } from "../types/instance";

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none";

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

  const textVision = instances.filter((i) => i.modality === "text" || i.modality === "vision");
  const embedding = instances.filter((i) => i.modality === "embedding");
  const image = instances.filter((i) => i.modality === "image");

  return (
    <select
      id="playground-model"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={inputClass}
    >
      {textVision.length > 0 && (
        <optgroup label="Text & Vision">
          {textVision.map((i) => (
            <option key={i.id} value={i.id}>
              {i.id}
            </option>
          ))}
        </optgroup>
      )}
      {embedding.length > 0 && (
        <optgroup label="Embedding">
          {embedding.map((i) => (
            <option key={i.id} value={i.id}>
              {i.id}
            </option>
          ))}
        </optgroup>
      )}
      {image.length > 0 && (
        <optgroup label="Image">
          {image.map((i) => (
            <option key={i.id} value={i.id}>
              {i.id}
            </option>
          ))}
        </optgroup>
      )}
    </select>
  );
}
