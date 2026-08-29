import { useMutation } from "@tanstack/react-query";
import { rootClient } from "./client";

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
 * other caller. Non-streaming only for this first version.
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
