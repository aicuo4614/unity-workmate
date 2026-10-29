/**
 * Chat SSE Service：stream / resume。
 * 为何用 fetch+ReadableStream 而不是 EventSource：要 POST JSON body（EventSource 只支持 GET）。
 */
import { API_BASE, type SessionPrefs } from "./types";

export type StreamHandler = (ev: Record<string, unknown>) => void;

async function* readSse(resp: Response): AsyncGenerator<Record<string, unknown>> {
  if (!resp.ok || !resp.body) {
    throw new Error(`stream HTTP ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n");
    buf = parts.pop() || "";
    for (const line of parts) {
      const t = line.trim();
      if (!t.startsWith("data: ")) continue;
      const data = t.slice(6);
      if (data === "[DONE]" || !data) continue;
      try {
        yield JSON.parse(data) as Record<string, unknown>;
      } catch {
        /* 忽略半包 */
      }
    }
  }
}

export async function streamChat(opts: {
  content: string;
  threadId: string;
  agentRole: string;
  sessionPrefs: SessionPrefs;
  onEvent: StreamHandler;
  signal?: AbortSignal;
}): Promise<void> {
  const messages = opts.content.trim()
    ? [{ role: "user", content: opts.content }]
    : [];
  const resp = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: opts.signal,
    body: JSON.stringify({
      messages,
      thread_id: opts.threadId,
      agent_role: opts.agentRole,
      session_prefs: opts.sessionPrefs,
    }),
  });
  for await (const ev of readSse(resp)) {
    opts.onEvent(ev);
  }
}

export async function streamResume(opts: {
  threadId: string;
  action: "say" | "ask" | "approve" | "skip" | "reject";
  stepIndex?: number;
  content?: string;
  onEvent: StreamHandler;
  signal?: AbortSignal;
}): Promise<void> {
  const resp = await fetch(`${API_BASE}/chat/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: opts.signal,
    body: JSON.stringify({
      thread_id: opts.threadId,
      action: opts.action,
      step_index: opts.stepIndex ?? 0,
      content: opts.content ?? "",
    }),
  });
  for await (const ev of readSse(resp)) {
    opts.onEvent(ev);
  }
}

export type ThreadGate = {
  waiting: boolean;
  /** hitl | act | null */
  gate: string | null;
  need_clarify: boolean;
  need_approval: boolean;
  plan_summary: string;
  phase: string | null;
};

export async function fetchThreadGate(threadId: string): Promise<ThreadGate> {
  const resp = await fetch(
    `${API_BASE}/chat/gate?thread_id=${encodeURIComponent(threadId)}`,
  );
  if (!resp.ok) {
    throw new Error(`gate HTTP ${resp.status}`);
  }
  return (await resp.json()) as ThreadGate;
}
