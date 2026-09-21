import { type ReactNode, useState } from "react";
import { useRegisterModel } from "../api/instances";
import { useToast } from "../context/ToastContext";
import { getErrorMessage } from "../lib/errors";
import { cn } from "../lib/cn";
import { ENGINE_LABELS, enginesAvailableOn } from "../lib/engines";
import type { InstanceEntry } from "../types/instance";
import type { Node } from "../types/node";

const inputClass =
  "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text " +
  "focus:border-primary focus:outline-none";

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block text-sm text-text">
      <span className="mb-1 block font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-text-muted">{hint}</span>}
    </label>
  );
}

/**
 * RM-70/RM-71: add another instance of a model that already exists.
 *
 * Deliberately not a third mode inside RegisterModelModal. Registering a new
 * *model* and adding a replica of an existing one are different jobs: the
 * first needs the file, the family, the quantization, the modality; the second
 * needs only what genuinely differs between two copies of the same weights —
 * which node runs it, on which engine, on which port. Everything else is
 * inherited from the catalog server-side, and the instance id and its `#N`
 * label are derived there too, so nobody has to invent a unique name.
 */
export function AddInstanceModal({
  open,
  source,
  nodes,
  onClose,
}: {
  open: boolean;
  /** The instance being replicated — only its model and node defaults are used. */
  source: InstanceEntry | null;
  nodes: Node[];
  onClose: () => void;
}) {
  const register = useRegisterModel();
  const { showToast } = useToast();
  const [node, setNode] = useState("");
  const [backendChoice, setBackendChoice] = useState<string>("");
  const [port, setPort] = useState("");
  const [contextLength, setContextLength] = useState("");

  if (!open || !source) return null;

  const modelId = source.model_id || source.id;
  const modelName = source.model_slug || modelId;
  const chosenNode = node || source.node;

  // PRM-133: what this node said it has, narrowed to what this build knows how
  // to launch. A node that never declared gets the whole list, which is what
  // this form did for every node before the declaration existed.
  const available = enginesAvailableOn(nodes.find((n) => n.name === chosenNode)?.engines);
  // Never a pre-selected value that isn't on offer: switching nodes can pull
  // the current choice out from under it, and a select whose value is absent
  // from its options renders blank while still submitting the old one.
  const backend = available.includes(backendChoice as (typeof available)[number])
    ? (backendChoice as (typeof available)[number])
    : (available[0] ?? null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (backend === null) {
      showToast(`${chosenNode} has no inference engine declared.`, "error");
      return;
    }
    try {
      const created = await register.mutateAsync({
        node: chosenNode,
        data: {
          // No `id`: manager-api derives the next free one. See its
          // register_backend — inventing unique ids by hand is what produced
          // the suffixes that leaked into the scope picker.
          model_id: modelId,
          port: Number(port),
          backend,
          discovery: true,
          ...(contextLength ? { context_length: Number(contextLength) } : {}),
        },
      });
      showToast(`Added ${created.label || created.id} to ${modelName}`, "success");
      onClose();
    } catch (error) {
      showToast(getErrorMessage(error), "error");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-md space-y-4 rounded-xl border border-border bg-surface p-6"
      >
        <div>
          <h2 className="text-lg font-semibold text-text">Add instance</h2>
          <p className="mt-1 text-sm text-text-muted">
            Another copy of <span className="font-medium text-text">{modelName}</span>. Clients keep
            sending the same model name — the gateway spreads requests across every instance.
          </p>
        </div>

        <Field label="Node">
          <select
            value={chosenNode}
            onChange={(e) => setNode(e.target.value)}
            className={inputClass}
          >
            {nodes.map((n) => (
              <option key={n.name} value={n.name}>
                {n.name}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Engine"
          hint={
            backend === null
              ? undefined
              : `Only what ${chosenNode} declared it has installed — set that list on the Nodes page.`
          }
        >
          {backend === null ? (
            <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-600">
              {chosenNode} declared no inference engines, so there is nothing to run this on. Add
              one on the Nodes page.
            </p>
          ) : (
            <select
              value={backend}
              onChange={(e) => setBackendChoice(e.target.value)}
              className={inputClass}
            >
              {available.map((b) => (
                <option key={b} value={b}>
                  {ENGINE_LABELS[b]}
                </option>
              ))}
            </select>
          )}
        </Field>

        <Field label="Port" hint="Must be free on the selected node.">
          <input
            type="number"
            min={1024}
            max={65535}
            required
            value={port}
            onChange={(e) => setPort(e.target.value)}
            className={inputClass}
          />
        </Field>

        <Field
          label="Context length"
          hint={`Leave blank to match ${source.context_length}. A smaller value is fine — the gateway validates against the smallest instance in the group.`}
        >
          <input
            type="number"
            placeholder={String(source.context_length)}
            value={contextLength}
            onChange={(e) => setContextLength(e.target.value)}
            className={inputClass}
          />
        </Field>

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-text-muted hover:bg-background"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={register.isPending || backend === null}
            className={cn(
              "rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground",
              register.isPending ? "opacity-60" : "hover:opacity-90",
            )}
          >
            {register.isPending ? "Adding…" : "Add instance"}
          </button>
        </div>
      </form>
    </div>
  );
}
