import { Copy, RotateCcw, Send, Trash2, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useInstances } from "../api/instances";
import {
  streamPlaygroundChat,
  usePlaygroundChat,
  type ChatMessage,
  type ToolCall,
  type ToolDefinition,
} from "../api/playground";
import { Sidebar } from "../components/Sidebar";
import { cn } from "../lib/cn";
import { getErrorMessage } from "../lib/errors";

/** Auto-scrolls to its own bottom as `text` grows — so the model's live chain-of-thought
 * stays visible instead of scrolling out of a fixed-height box (RM-36 follow-up). */
function ReasoningBox({ text }: { text: string }) {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "end" });
  }, [text]);
  return (
    <div className="max-w-[80%] rounded-xl border border-dashed border-border bg-surface px-4 py-2 text-xs text-text-muted">
      <p className="mb-1 font-medium">🧠 Thinking…</p>
      <div className="max-h-40 overflow-y-auto">
        <p className="whitespace-pre-wrap italic">
          {text}
          <span className="animate-pulse">▍</span>
        </p>
        <div ref={ref} />
      </div>
    </div>
  );
}

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text focus:border-primary focus:outline-none";

interface Turn {
  // Leading message(s) — either one "user" message, or one "tool" message per
  // pending tool_call being answered — followed by the assistant's response.
  messages: ChatMessage[];
  // null for a streamed response — the backends verified so far don't report
  // usage for stream:true (docs/roadmap.md RM-36).
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number } | null;
  latencyMs: number;
  finishReason: string | null;
  // The model's chain-of-thought for this turn, if any — kept around (collapsed
  // in the UI) rather than discarded, especially useful when it ran out of
  // max_tokens before producing a visible answer.
  reasoning: string;
}

interface InProgress {
  leading: ChatMessage[];
  content: string;
  reasoning: string;
  toolCalls: ToolCall[];
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
  const [toolsInput, setToolsInput] = useState("");
  const [toolChoice, setToolChoice] = useState<"auto" | "required" | "none">("auto");
  const [toolsError, setToolsError] = useState<string | null>(null);
  const [toolResultDrafts, setToolResultDrafts] = useState<Record<string, string>>({});
  const [streamingEnabled, setStreamingEnabled] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [inProgress, setInProgress] = useState<InProgress | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);

  const runningTextModels = (instancesQuery.data?.instances ?? []).filter(
    (i) => i.state === "ready" && i.modality === "text",
  );
  const selectedModel = model || runningTextModels[0]?.id || "";

  /** Returns null (and sets toolsError) if the JSON is present but invalid. */
  function parseTools(): ToolDefinition[] | null | undefined {
    if (!toolsInput.trim()) {
      setToolsError(null);
      return undefined;
    }
    try {
      const parsed = JSON.parse(toolsInput);
      if (!Array.isArray(parsed)) throw new Error("Tools must be a JSON array.");
      setToolsError(null);
      return parsed as ToolDefinition[];
    } catch (e) {
      setToolsError(e instanceof Error ? e.message : "Invalid JSON.");
      return null;
    }
  }

  function buildParams(tools: ToolDefinition[] | undefined) {
    const stop = stopInput
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    return {
      temperature,
      top_p: topP,
      max_tokens: maxTokens,
      ...(stop.length > 0 ? { stop } : {}),
      ...(tools ? { tools, tool_choice: toolChoice } : {}),
    };
  }

  function historyMessages(): ChatMessage[] {
    const history = turns.flatMap((t) => t.messages);
    return systemPrompt.trim()
      ? [{ role: "system" as const, content: systemPrompt }, ...history]
      : history;
  }

  async function sendNonStreaming(leadingMessages: ChatMessage[], messages: ChatMessage[], tools: ToolDefinition[] | undefined) {
    const startedAt = performance.now();
    const data = await chat.mutateAsync({ model: selectedModel, messages, params: buildParams(tools) });
    const latencyMs = Math.round(performance.now() - startedAt);
    const responseMessage = data.choices[0]?.message;
    const hasToolCalls = (responseMessage?.tool_calls?.length ?? 0) > 0;
    const assistantMessage: ChatMessage = {
      role: "assistant",
      // Same rule as the streaming path: null content is only valid when
      // tool_calls carries the payload instead, or the backend never gets
      // sent as history on a later turn.
      content: hasToolCalls ? null : (responseMessage?.content ?? ""),
      tool_calls: responseMessage?.tool_calls,
    };
    setTurns((prev) => [
      ...prev,
      {
        messages: [...leadingMessages, assistantMessage],
        usage: data.usage,
        latencyMs,
        finishReason: data.choices[0]?.finish_reason ?? null,
        reasoning: responseMessage?.reasoning_content ?? "",
      },
    ]);
  }

  async function sendStreaming(leadingMessages: ChatMessage[], messages: ChatMessage[], tools: ToolDefinition[] | undefined) {
    const startedAt = performance.now();
    let content = "";
    let reasoning = "";
    const toolCallsByIndex = new Map<number, ToolCall>();
    setInProgress({ leading: leadingMessages, content: "", reasoning: "", toolCalls: [] });

    const { finishReason, usage } = await streamPlaygroundChat(
      { model: selectedModel, messages, params: buildParams(tools) },
      (delta) => {
        if (delta.content) content += delta.content;
        if (delta.reasoning_content) reasoning += delta.reasoning_content;
        for (const partial of delta.tool_calls ?? []) {
          const existing = toolCallsByIndex.get(partial.index) ?? {
            id: "",
            type: "function" as const,
            function: { name: "", arguments: "" },
          };
          if (partial.id) existing.id = partial.id;
          if (partial.function?.name) existing.function.name = partial.function.name;
          if (partial.function?.arguments) existing.function.arguments += partial.function.arguments;
          toolCallsByIndex.set(partial.index, existing);
        }
        setInProgress({ leading: leadingMessages, content, reasoning, toolCalls: [...toolCallsByIndex.values()] });
        requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ block: "end" }));
      },
    );

    const latencyMs = Math.round(performance.now() - startedAt);
    const toolCalls = [...toolCallsByIndex.values()];
    const assistantMessage: ChatMessage = {
      role: "assistant",
      // OpenAI's shape only allows null content when tool_calls carries the
      // payload instead — an assistant message with neither is invalid and
      // gets the *entire conversation* rejected by the backend on every
      // later turn. An empty-but-present string (e.g. ran out of max_tokens
      // during reasoning) must stay a string, never null.
      content: toolCalls.length > 0 ? null : content,
      tool_calls: toolCalls.length > 0 ? toolCalls : undefined,
    };
    setInProgress(null);
    setTurns((prev) => [
      ...prev,
      { messages: [...leadingMessages, assistantMessage], usage, latencyMs, finishReason, reasoning },
    ]);
  }

  async function sendMessages(leadingMessages: ChatMessage[]) {
    if (!selectedModel || isSending) return;
    const tools = parseTools();
    if (tools === null) return; // invalid JSON — toolsError is already set
    setSendError(null);
    setIsSending(true);
    const messages = [...historyMessages(), ...leadingMessages];
    try {
      if (streamingEnabled) {
        await sendStreaming(leadingMessages, messages, tools);
      } else {
        await sendNonStreaming(leadingMessages, messages, tools);
      }
      requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ block: "end" }));
    } catch (error) {
      setInProgress(null);
      setSendError(getErrorMessage(error));
    } finally {
      setIsSending(false);
    }
  }

  function handleSend() {
    if (!draft.trim() || isSending) return;
    const userMessage: ChatMessage = { role: "user", content: draft };
    setDraft("");
    void sendMessages([userMessage]);
  }

  function handleRegenerate() {
    if (turns.length === 0 || isSending) return;
    const lastLeading = turns[turns.length - 1].messages.slice(0, -1);
    setTurns((prev) => prev.slice(0, -1));
    void sendMessages(lastLeading);
  }

  /** RM-35 follow-up: the model asked to call a tool — the Playground has no real
   * tool executor, so the operator types a mock result per call to continue the
   * conversation and see the model's actual final answer. */
  function handleSubmitToolResults(calls: ChatMessage["tool_calls"]) {
    if (!calls || calls.length === 0 || isSending) return;
    const toolMessages: ChatMessage[] = calls.map((call) => ({
      role: "tool",
      tool_call_id: call.id,
      content: toolResultDrafts[call.id]?.trim() || "(no result provided)",
    }));
    setToolResultDrafts({});
    void sendMessages(toolMessages);
  }

  function handleClear() {
    setTurns([]);
    setSendError(null);
  }

  async function handleCopy(message: ChatMessage) {
    const text = message.content ?? JSON.stringify(message.tool_calls, null, 2);
    await navigator.clipboard.writeText(text ?? "");
  }

  async function handleCopyReasoning(reasoning: string) {
    await navigator.clipboard.writeText(reasoning);
  }

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar />
      <main className="flex min-h-0 min-w-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col px-8 py-8">
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

          <div className="mt-4 min-h-0 flex-1 space-y-4 overflow-y-auto rounded-xl border border-border bg-surface p-4">
            {turns.length === 0 ? (
              <p className="text-sm text-text-muted">No messages yet — send a prompt to get started.</p>
            ) : (
              turns.map((turn, i) => {
                const leading = turn.messages.slice(0, -1);
                const assistantMessage = turn.messages[turn.messages.length - 1];
                const isLastTurn = i === turns.length - 1;
                return (
                <div key={i} className="space-y-3">
                  {leading.map((m, j) =>
                    m.role === "tool" ? (
                      <div
                        key={j}
                        className="ml-auto max-w-[80%] rounded-xl border border-dashed border-border bg-surface px-4 py-2 text-xs text-text-muted"
                      >
                        Tool result: {m.content}
                      </div>
                    ) : (
                      <div
                        key={j}
                        className="ml-auto max-w-[80%] rounded-xl bg-primary px-4 py-2 text-sm text-primary-foreground"
                      >
                        {m.content}
                      </div>
                    ),
                  )}
                  <div className="max-w-[80%] rounded-xl border border-border bg-background px-4 py-2 text-sm text-text">
                    {assistantMessage.content && (
                      <p className="whitespace-pre-wrap">{assistantMessage.content}</p>
                    )}
                    {!assistantMessage.content &&
                      !assistantMessage.tool_calls?.length &&
                      turn.finishReason === "length" && (
                        <p className="text-amber-600">
                          Ran out of max tokens before producing a visible answer — this model
                          spends tokens on hidden reasoning first, and used up the whole budget
                          there. Try raising Max tokens.
                        </p>
                      )}
                    {turn.reasoning && (
                      <details className="mt-2 rounded-lg border border-dashed border-border bg-surface p-2 text-xs text-text-muted">
                        <summary className="cursor-pointer select-none font-medium">
                          Show the model's reasoning ({turn.reasoning.length.toLocaleString()} chars)
                        </summary>
                        <div className="mt-2 flex items-start justify-between gap-2">
                          <p className="max-h-64 overflow-y-auto whitespace-pre-wrap italic">
                            {turn.reasoning}
                          </p>
                          <button
                            type="button"
                            onClick={() => handleCopyReasoning(turn.reasoning)}
                            title="Copy reasoning"
                            className="shrink-0 text-text-muted hover:text-text"
                          >
                            <Copy size={14} />
                          </button>
                        </div>
                      </details>
                    )}
                    {assistantMessage.tool_calls?.map((call) => (
                      <div
                        key={call.id}
                        className="mt-1 flex items-start gap-2 rounded-lg bg-surface p-2 font-mono text-xs text-text"
                      >
                        <Wrench size={14} className="mt-0.5 shrink-0 text-primary" />
                        <div>
                          <span className="font-semibold">{call.function.name}</span>
                          <span className="text-text-muted">({call.function.arguments})</span>
                        </div>
                      </div>
                    ))}
                    <div className="mt-2 flex items-center gap-3 border-t border-border pt-2 text-xs text-text-muted">
                      <span>
                        {turn.usage
                          ? `${turn.usage.prompt_tokens} + ${turn.usage.completion_tokens} = ${turn.usage.total_tokens} tokens`
                          : "tokens not reported (streamed)"}
                      </span>
                      <span>{turn.latencyMs} ms</span>
                      <button
                        type="button"
                        onClick={() => handleCopy(assistantMessage)}
                        title="Copy response"
                        className="ml-auto text-text-muted hover:text-text"
                      >
                        <Copy size={14} />
                      </button>
                      {isLastTurn && (
                        <button
                          type="button"
                          onClick={handleRegenerate}
                          disabled={isSending}
                          title="Regenerate"
                          className="text-text-muted hover:text-text disabled:opacity-40"
                        >
                          <RotateCcw size={14} />
                        </button>
                      )}
                    </div>
                  </div>
                  {isLastTurn && assistantMessage.tool_calls && assistantMessage.tool_calls.length > 0 && (
                    <div className="max-w-[80%] space-y-2 rounded-xl border border-dashed border-border bg-surface p-3">
                      <p className="text-xs text-text-muted">
                        The Playground doesn't execute tools for real — type a mock result to
                        continue and see the model's final answer.
                      </p>
                      {assistantMessage.tool_calls.map((call) => (
                        <div key={call.id} className="flex items-center gap-2">
                          <span className="shrink-0 font-mono text-xs text-text-muted">
                            {call.function.name}:
                          </span>
                          <input
                            type="text"
                            value={toolResultDrafts[call.id] ?? ""}
                            onChange={(e) =>
                              setToolResultDrafts((prev) => ({ ...prev, [call.id]: e.target.value }))
                            }
                            placeholder="Mock result, e.g. 22C, sunny"
                            className={cn(inputClass, "text-xs")}
                          />
                        </div>
                      ))}
                      <button
                        type="button"
                        onClick={() => handleSubmitToolResults(assistantMessage.tool_calls)}
                        disabled={isSending}
                        className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        Submit result
                      </button>
                    </div>
                  )}
                </div>
                );
              })
            )}
            {isSending && !inProgress && (
              <p className="text-sm text-text-muted">Waiting for a response…</p>
            )}
            {inProgress &&
              inProgress.leading.map((m, j) =>
                m.role === "tool" ? (
                  <div
                    key={j}
                    className="ml-auto max-w-[80%] rounded-xl border border-dashed border-border bg-surface px-4 py-2 text-xs text-text-muted"
                  >
                    Tool result: {m.content}
                  </div>
                ) : (
                  <div
                    key={j}
                    className="ml-auto max-w-[80%] rounded-xl bg-primary px-4 py-2 text-sm text-primary-foreground"
                  >
                    {m.content}
                  </div>
                ),
              )}
            {inProgress &&
              !inProgress.content &&
              inProgress.toolCalls.length === 0 &&
              inProgress.reasoning && <ReasoningBox text={inProgress.reasoning} />}
            {inProgress && (inProgress.content || inProgress.toolCalls.length > 0) && (
              <div className="max-w-[80%] rounded-xl border border-border bg-background px-4 py-2 text-sm text-text">
                {inProgress.content && (
                  <p className="whitespace-pre-wrap">
                    {inProgress.content}
                    <span className="animate-pulse">▍</span>
                  </p>
                )}
                {inProgress.toolCalls.map((call, idx) => (
                  <div
                    key={idx}
                    className="mt-1 flex items-start gap-2 rounded-lg bg-surface p-2 font-mono text-xs text-text"
                  >
                    <Wrench size={14} className="mt-0.5 shrink-0 text-primary" />
                    <div>
                      <span className="font-semibold">{call.function.name || "…"}</span>
                      <span className="text-text-muted">({call.function.arguments})</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {inProgress && !inProgress.content && !inProgress.reasoning && inProgress.toolCalls.length === 0 && (
              <p className="text-sm text-text-muted">Streaming…</p>
            )}
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
              disabled={!selectedModel || !draft.trim() || isSending}
              className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Send size={16} />
              Send
            </button>
          </div>
        </div>

        <aside className="w-72 shrink-0 space-y-5 overflow-y-auto border-l border-border bg-surface px-5 py-8">
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

          <label className="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              checked={streamingEnabled}
              onChange={(e) => setStreamingEnabled(e.target.checked)}
            />
            Stream response
          </label>

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

          <h2 className="text-xs font-medium uppercase tracking-wide text-text-muted">
            Tools (function calling)
          </h2>

          <div>
            <label htmlFor="playground-tools" className="mb-1.5 block text-sm text-text">
              Tool definitions (JSON)
            </label>
            <textarea
              id="playground-tools"
              rows={6}
              value={toolsInput}
              onChange={(e) => setToolsInput(e.target.value)}
              placeholder={'Optional — an OpenAI-style tools array, e.g.\n[\n  {\n    "type": "function",\n    "function": {\n      "name": "get_weather",\n      "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}\n    }\n  }\n]'}
              className={cn(inputClass, "font-mono text-xs")}
            />
            {toolsError && <p className="mt-1 text-xs text-red-600">{toolsError}</p>}
          </div>

          <div>
            <label htmlFor="playground-tool-choice" className="mb-1.5 block text-sm text-text">
              Tool choice
            </label>
            <select
              id="playground-tool-choice"
              value={toolChoice}
              onChange={(e) => setToolChoice(e.target.value as "auto" | "required" | "none")}
              disabled={!toolsInput.trim()}
              className={cn(inputClass, !toolsInput.trim() && "opacity-40")}
            >
              <option value="auto">auto</option>
              <option value="required">required</option>
              <option value="none">none</option>
            </select>
            {toolChoice === "required" && (
              <p className="mt-1 text-xs text-text-muted">
                "required" forces a tool call every turn — switch to "auto" after submitting a
                result if you want the model's final text answer instead of another call.
              </p>
            )}
          </div>
        </aside>
      </main>
    </div>
  );
}
