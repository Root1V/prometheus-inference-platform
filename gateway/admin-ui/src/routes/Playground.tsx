import { Copy, RotateCcw, Send, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { useInstances } from "../api/instances";
import { usePlaygroundChat, type ChatMessage } from "../api/playground";
import { Sidebar } from "../components/Sidebar";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none";

interface Turn {
  messages: ChatMessage[]; // [user, assistant] once complete
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  latencyMs: number;
}

export default function Playground() {
  const instancesQuery = useInstances();
  const chat = usePlaygroundChat();

  const [model, setModel] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sendError, setSendError] = useState<string | null>(null);

  const [temperature, setTemperature] = useState(1.0);
  const [topP, setTopP] = useState(1.0);
  const [maxTokens, setMaxTokens] = useState(512);
  const [stopInput, setStopInput] = useState("");

  const bottomRef = useRef<HTMLDivElement>(null);

  const runningTextModels = (instancesQuery.data?.instances ?? []).filter(
    (i) => i.state === "ready" && i.modality === "text",
  );
  const selectedModel = model || runningTextModels[0]?.id || "";

  function buildParams() {
    const stop = stopInput
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    return {
      temperature,
      top_p: topP,
      max_tokens: maxTokens,
      ...(stop.length > 0 ? { stop } : {}),
    };
  }

  function historyMessages(): ChatMessage[] {
    const history = turns.flatMap((t) => t.messages);
    return systemPrompt.trim()
      ? [{ role: "system" as const, content: systemPrompt }, ...history]
      : history;
  }

  async function sendMessages(userMessage: ChatMessage) {
    if (!selectedModel) return;
    setSendError(null);
    const messages = [...historyMessages(), userMessage];
    const startedAt = performance.now();
    try {
      const data = await chat.mutateAsync({ model: selectedModel, messages, params: buildParams() });
      const latencyMs = Math.round(performance.now() - startedAt);
      const assistantMessage: ChatMessage = {
        role: "assistant",
        content: data.choices[0]?.message.content ?? "",
      };
      setTurns((prev) => [...prev, { messages: [userMessage, assistantMessage], usage: data.usage, latencyMs }]);
      requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ block: "end" }));
    } catch (error) {
      setSendError(getErrorMessage(error));
    }
  }

  function handleSend() {
    if (!draft.trim() || chat.isPending) return;
    const userMessage: ChatMessage = { role: "user", content: draft };
    setDraft("");
    void sendMessages(userMessage);
  }

  function handleRegenerate() {
    if (turns.length === 0 || chat.isPending) return;
    const lastUserMessage = turns[turns.length - 1].messages[0];
    setTurns((prev) => prev.slice(0, -1));
    void sendMessages(lastUserMessage);
  }

  function handleClear() {
    setTurns([]);
    setSendError(null);
  }

  async function handleCopy(content: string) {
    await navigator.clipboard.writeText(content);
  }

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <main className="flex min-w-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col px-8 py-8">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-semibold text-text">Playground</h1>
              <p className="mt-1 text-sm text-text-muted">
                Sends real requests through the gateway's inference API — this counts as
                real usage, recorded the same as any other call.
              </p>
            </div>
            <button
              type="button"
              onClick={handleClear}
              disabled={turns.length === 0}
              className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:bg-surface disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Trash2 size={14} />
              Clear
            </button>
          </div>

          <div className="mt-4">
            <label htmlFor="playground-system" className="mb-1.5 block text-sm font-medium text-text">
              System prompt
            </label>
            <textarea
              id="playground-system"
              rows={2}
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="Optional — sets the assistant's behavior for this conversation."
              className={inputClass}
            />
          </div>

          <div className="mt-4 flex-1 space-y-4 overflow-y-auto rounded-xl border border-border bg-surface p-4">
            {turns.length === 0 ? (
              <p className="text-sm text-text-muted">No messages yet — send a prompt to get started.</p>
            ) : (
              turns.map((turn, i) => (
                <div key={i} className="space-y-3">
                  <div className="ml-auto max-w-[80%] rounded-xl bg-primary px-4 py-2 text-sm text-primary-foreground">
                    {turn.messages[0].content}
                  </div>
                  <div className="max-w-[80%] rounded-xl border border-border bg-background px-4 py-2 text-sm text-text">
                    <p className="whitespace-pre-wrap">{turn.messages[1].content}</p>
                    <div className="mt-2 flex items-center gap-3 border-t border-border pt-2 text-xs text-text-muted">
                      <span>
                        {turn.usage.prompt_tokens} + {turn.usage.completion_tokens} ={" "}
                        {turn.usage.total_tokens} tokens
                      </span>
                      <span>{turn.latencyMs} ms</span>
                      <button
                        type="button"
                        onClick={() => handleCopy(turn.messages[1].content)}
                        title="Copy response"
                        className="ml-auto text-text-muted hover:text-text"
                      >
                        <Copy size={14} />
                      </button>
                      {i === turns.length - 1 && (
                        <button
                          type="button"
                          onClick={handleRegenerate}
                          disabled={chat.isPending}
                          title="Regenerate"
                          className="text-text-muted hover:text-text disabled:opacity-40"
                        >
                          <RotateCcw size={14} />
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))
            )}
            {chat.isPending && <p className="text-sm text-text-muted">Waiting for a response…</p>}
            <div ref={bottomRef} />
          </div>

          {sendError && (
            <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {sendError}
            </div>
          )}

          <div className="mt-4 flex items-end gap-2">
            <textarea
              rows={2}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder={
                runningTextModels.length === 0
                  ? "No running text models — start one from Instances first."
                  : "Ask something… (Enter to send, Shift+Enter for a new line)"
              }
              disabled={runningTextModels.length === 0}
              className={cn(inputClass, "flex-1")}
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={!selectedModel || !draft.trim() || chat.isPending}
              className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Send size={16} />
              Send
            </button>
          </div>
        </div>

        <aside className="w-72 shrink-0 space-y-5 border-l border-border bg-surface px-5 py-8">
          <div>
            <label htmlFor="playground-model" className="mb-1.5 block text-sm font-medium text-text">
              Model
            </label>
            {runningTextModels.length === 0 ? (
              <p className="text-sm text-text-muted">No running text models.</p>
            ) : (
              <select
                id="playground-model"
                value={selectedModel}
                onChange={(e) => setModel(e.target.value)}
                className={inputClass}
              >
                {runningTextModels.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.id}
                  </option>
                ))}
              </select>
            )}
          </div>

          <h2 className="text-xs font-medium uppercase tracking-wide text-text-muted">Parameters</h2>

          <div>
            <div className="mb-1.5 flex items-center justify-between text-sm text-text">
              <label htmlFor="playground-temperature">Temperature</label>
              <span className="text-text-muted">{temperature.toFixed(1)}</span>
            </div>
            <input
              id="playground-temperature"
              type="range"
              min={0}
              max={2}
              step={0.1}
              value={temperature}
              onChange={(e) => setTemperature(Number(e.target.value))}
              className="w-full"
            />
          </div>

          <div>
            <div className="mb-1.5 flex items-center justify-between text-sm text-text">
              <label htmlFor="playground-top-p">Top P</label>
              <span className="text-text-muted">{topP.toFixed(2)}</span>
            </div>
            <input
              id="playground-top-p"
              type="range"
              min={0.05}
              max={1}
              step={0.05}
              value={topP}
              onChange={(e) => setTopP(Number(e.target.value))}
              className="w-full"
            />
          </div>

          <div>
            <label htmlFor="playground-max-tokens" className="mb-1.5 block text-sm text-text">
              Max tokens
            </label>
            <input
              id="playground-max-tokens"
              type="number"
              min={1}
              value={maxTokens}
              onChange={(e) => setMaxTokens(Math.max(1, Number(e.target.value) || 1))}
              className={inputClass}
            />
          </div>

          <div>
            <label htmlFor="playground-stop" className="mb-1.5 block text-sm text-text">
              Stop sequences
            </label>
            <input
              id="playground-stop"
              type="text"
              value={stopInput}
              onChange={(e) => setStopInput(e.target.value)}
              placeholder="Comma-separated, optional"
              className={inputClass}
            />
          </div>
        </aside>
      </main>
    </div>
  );
}
