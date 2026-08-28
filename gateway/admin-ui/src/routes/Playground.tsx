import { Send } from "lucide-react";
import { useState } from "react";
import { useInstances } from "../api/instances";
import { usePlaygroundChat } from "../api/playground";
import { Sidebar } from "../components/Sidebar";
import { getErrorMessage } from "../lib/errors";

export default function Playground() {
  const instancesQuery = useInstances();
  const chat = usePlaygroundChat();
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");

  const runningTextModels = (instancesQuery.data?.instances ?? []).filter(
    (i) => i.state === "ready" && i.modality === "text",
  );
  const selectedModel = model || runningTextModels[0]?.id || "";
  const canSend = selectedModel !== "" && prompt.trim() !== "" && !chat.isPending;

  function handleSend() {
    if (!canSend) return;
    chat.mutate({ model: selectedModel, prompt });
  }

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="flex-1 px-8 py-8">
        <h1 className="text-2xl font-semibold text-text">Playground</h1>
        <p className="mt-1 text-sm text-text-muted">
          Send a test prompt to a running model through the gateway's real inference API —
          this counts as real usage, recorded the same as any other call.
        </p>

        <div className="mt-6 max-w-2xl space-y-4">
          <div>
            <label htmlFor="playground-model" className="mb-1.5 block text-sm font-medium text-text">
              Model
            </label>
            {runningTextModels.length === 0 ? (
              <p className="text-sm text-text-muted">
                No running text models — start one from the Instances page first.
              </p>
            ) : (
              <select
                id="playground-model"
                value={selectedModel}
                onChange={(e) => setModel(e.target.value)}
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
              >
                {runningTextModels.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.id}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div>
            <label htmlFor="playground-prompt" className="mb-1.5 block text-sm font-medium text-text">
              Prompt
            </label>
            <textarea
              id="playground-prompt"
              rows={4}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Ask something…"
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
            />
          </div>

          <button
            type="button"
            onClick={handleSend}
            disabled={!canSend}
            className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Send size={16} />
            {chat.isPending ? "Sending…" : "Send"}
          </button>

          {chat.isError && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
              {getErrorMessage(chat.error)}
            </div>
          )}

          {chat.isSuccess && (
            <div className="rounded-xl border border-border bg-surface p-4">
              <p className="whitespace-pre-wrap text-sm text-text">
                {chat.data.choices[0]?.message.content}
              </p>
              <p className="mt-3 border-t border-border pt-3 text-xs text-text-muted">
                {chat.data.usage.prompt_tokens} prompt + {chat.data.usage.completion_tokens}{" "}
                completion = {chat.data.usage.total_tokens} tokens
              </p>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
