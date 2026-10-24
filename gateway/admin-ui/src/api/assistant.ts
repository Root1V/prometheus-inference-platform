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

/** `page` is the route the operator is on. The gateway maps it to a fixed
 *  description and drops one it does not know, so it is a key, never text that
 *  reaches the model as written. */
export async function askAssistant(
  messages: AssistantMessage[],
  page: string,
): Promise<AssistantReply> {
  const { data } = await apiClient.post<AssistantReply>("/assistant/chat", {
    messages,
    page,
  });
  return data;
}
