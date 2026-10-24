import { apiClient } from "./client";

/** One turn of the conversation, as the gateway takes it — PRM-272. Only the
 *  operator's words and the assistant's answers travel; the system prompt and
 *  the tool round-trips live server-side and never reach the page. */
export interface AssistantMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AssistantReply {
  content: string;
  /** Which tools it called to answer, in order. */
  tool_calls: string[];
  rounds: number;
  elapsed_s: number;
}

export async function askAssistant(
  messages: AssistantMessage[],
): Promise<AssistantReply> {
  const { data } = await apiClient.post<AssistantReply>("/assistant/chat", {
    messages,
  });
  return data;
}
