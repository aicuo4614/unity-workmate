import type { SessionPrefs } from "./types";
import { ROLE_DEFS } from "../roles/registry";

/**
 * 本机会话登记（localStorage）。
 * 为何不放 server：产品拍板 v1 本地即可；thread_id 仍是服务端 checkpoint 键。
 * 重名规则：同 agentRole 下 displayName 唯一。
 * 写入时机：进入对话页（感知/诊断…）时，不是选助手/连接时。
 */
export type SavedSession = {
  threadId: string;
  displayName: string;
  agentRole: string;
  updatedAt: number;
  mcpUrl?: string;
  prefs?: SessionPrefs;
  /** 对话页事件流快照（本机）；服务端 checkpoint 另存 messages */
  feed?: Array<{ id: string; kind: string; text: string; meta?: string }>;
  phase?: string | null;
  started?: boolean;
  /** 离开对话页时是否卡在 hitl（本机；进页再以服务端 checkpoint 为准） */
  needClarify?: boolean;
  clarifyGate?: "hitl" | "clarify_hitl" | "advise" | null;
  needApproval?: boolean;
  planSummary?: string;
  /** 本局累计 LLM token（本机累加） */
  promptTokens?: number;
  completionTokens?: number;
};

const STORAGE_KEY = "ailearning.sessions.v1";

/** 扮演元数据：展示名 key、默认 MCP（不同助手可不同）。来自 roles/registry。 */
export const ROLE_META: Record<
  string,
  { labelKey: string; defaultMcpUrl: string; namePrefixKey: string }
> = Object.fromEntries(
  ROLE_DEFS.map((role) => [
    role.id,
    {
      labelKey: role.labelKey,
      defaultMcpUrl: role.defaultMcpUrl,
      namePrefixKey: role.namePrefixKey,
    },
  ]),
);

function readAll(): SavedSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as SavedSession[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeAll(list: SavedSession[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
}

export function listSessions(agentRole?: string): SavedSession[] {
  const all = readAll();
  const filtered = agentRole ? all.filter((s) => s.agentRole === agentRole) : all;
  return filtered.sort((a, b) => b.updatedAt - a.updatedAt);
}

export function isNameTaken(
  agentRole: string,
  displayName: string,
  exceptThreadId?: string,
): boolean {
  const name = displayName.trim();
  if (!name) return true;
  return listSessions(agentRole).some(
    (s) => s.displayName === name && s.threadId !== exceptThreadId,
  );
}

/** 同助手下生成不重名默认名：前缀 + 空格 + 序号 */
export function suggestDisplayName(agentRole: string, prefix: string): string {
  const base = prefix.trim() || agentRole;
  const existing = new Set(listSessions(agentRole).map((s) => s.displayName));
  let n = 1;
  while (existing.has(`${base} ${n}`)) n += 1;
  return `${base} ${n}`;
}

export function upsertSession(entry: SavedSession): void {
  const all = readAll().filter((s) => s.threadId !== entry.threadId);
  all.push({ ...entry, displayName: entry.displayName.trim() });
  writeAll(all);
}

export function touchSession(threadId: string, patch?: Partial<SavedSession>): void {
  const all = readAll();
  const i = all.findIndex((s) => s.threadId === threadId);
  if (i < 0) return;
  all[i] = { ...all[i], ...patch, updatedAt: Date.now() };
  writeAll(all);
}

export function getSessionRecord(threadId: string): SavedSession | undefined {
  return readAll().find((s) => s.threadId === threadId);
}

export function deleteSession(threadId: string): void {
  writeAll(readAll().filter((s) => s.threadId !== threadId));
}

export function defaultMcpForRole(agentRole: string): string {
  return ROLE_META[agentRole]?.defaultMcpUrl || "http://127.0.0.1:3828/mcp";
}
