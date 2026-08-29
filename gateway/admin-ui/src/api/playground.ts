import { useMutation } from "@tanstack/react-query";
import { getStoredToken } from "./auth";
import { AUTH_EXPIRED_EVENT, rootClient } from "./client";

export interface ToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
}

export interface ToolDefinition {
  type: "function";
  function: { name: string; description?: string; parameters?: object };
}

export interface PlaygroundParams {
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  stop?: string[];
  tools?: ToolDefinition[];
  tool_choice?: string;
}

interface ChatCompletionResponse {
  choices: {
    message: { role: string; content: string | null; tool_calls?: ToolCall[] };
    finish_reason: string | null;
  }[];
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
}

/**
 * RM-14: calls the gateway's real POST /v1/chat/completions with the admin
 * dashboard's own session token — the same endpoint real clients use, so
 * usage/cost recording and metrics all apply exactly as they would for any
 * other caller.
 */
export function usePlaygroundChat() {
  return useMutation({
    mutationFn: async ({
      model,
      messages,
      params,
    }: {
      model: string;
      messages: ChatMessage[];
      params: PlaygroundParams;
    }) =>
      (
        await rootClient.post<ChatCompletionResponse>("/v1/chat/completions", {
          model,
          messages,
          stream: false,
          ...params,
        })
      ).data,
  });
}

/** A partial tool_calls entry as it arrives in a streaming delta — same shape
 * OpenAI/llama.cpp use: only `index` is guaranteed on every chunk; `id`/
 * `function.name` arrive once, `function.arguments` arrives in fragments. */
export interface ToolCallDelta {
  index: number;
  id?: string;
  function?: { name?: string; arguments?: string };
}

export interface StreamDelta {
  content?: string;
  /** gpt-oss-style chain-of-thought, sent as its own delta field before any
   * real `content` — surfaced live so a long reasoning phase doesn't look
   * frozen (docs/roadmap.md RM-36 follow-up). */
  reasoning_content?: string;
  tool_calls?: ToolCallDelta[];
}

/**
 * RM-36: same real endpoint as usePlaygroundChat, but with stream: true,
 * parsed as Server-Sent Events and reported incrementally via onDelta — not a
 * react-query mutation, since progressive UI updates don't fit that model.
 * The gateway doesn't report `usage` for a streamed response with the
 * backends verified so far, so there's no token count to return here.
 */
export async function streamPlaygroundChat(
  { model, messages, params }: { model: string; messages: ChatMessage[]; params: PlaygroundParams },
  onDelta: (delta: StreamDelta) => void,
): Promise<{ finishReason: string | null }> {
  const token = getStoredToken();
  const response = await fetch("/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ model, messages, stream: true, ...params }),
  });

  if (response.status === 401) {
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }
  if (!response.ok || !response.body) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore — keep the generic message */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finishReason: string | null = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const line = event.trim();
      if (!line.startsWith("data:")) {
        // llama.cpp can commit to a 200 streaming response and then write a
        // plain (non-SSE-prefixed) {"error": {...}} line if the request turns
        // out invalid mid-stream (e.g. malformed conversation history) —
        // surface it instead of silently dropping it as an unrecognized line.
        if (line.startsWith("{") && line.includes('"error"')) {
          let message: string | undefined;
          try {
            message = JSON.parse(line)?.error?.message;
          } catch {
            /* not valid JSON after all — fall through and ignore the line */
          }
          if (message) throw new Error(message);
        }
        continue;
      }
      const data = line.slice(5).trim();
      if (data === "[DONE]" || !data) continue;
      let chunk: { choices?: { delta?: StreamDelta; finish_reason?: string | null }[] };
      try {
        chunk = JSON.parse(data);
      } catch {
        continue;
      }
      const choice = chunk.choices?.[0];
      if (!choice) continue;
      if (choice.finish_reason) finishReason = choice.finish_reason;
      if (choice.delta) onDelta(choice.delta);
    }
  }

  return { finishReason };
}
