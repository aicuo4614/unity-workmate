/**
 * Health / MCP 重连 Service：唯一碰 /health 与 /mcp/reconnect 的地方。
 * 组件禁止直接 fetch。
 */
import { API_BASE, type Health } from "./types";

export async function fetchHealth(): Promise<Health> {
  const resp = await fetch(`${API_BASE}/health`);
  if (!resp.ok) throw new Error(`health HTTP ${resp.status}`);
  return resp.json();
}

export async function reconnectMcp(
  url: string,
  persist = true,
): Promise<Health & { ok: boolean }> {
  const resp = await fetch(`${API_BASE}/mcp/reconnect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, persist }),
  });
  if (!resp.ok) throw new Error(`reconnect HTTP ${resp.status}`);
  return resp.json();
}
