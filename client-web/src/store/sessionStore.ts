import type { Health, SessionPrefs } from "../services/types";
import { defaultPrefs } from "../services/types";
import {
  suggestDisplayName,
  upsertSession,
  type SavedSession,
  ROLE_META,
} from "../services/sessionRegistry";
import { stripToolMarkup } from "../utils/stripToolMarkup";
import { clientLog } from "../utils/clientLog";

export type FeedItem = {
  id: string;
  kind: "phase" | "token" | "tool" | "observation" | "hitl" | "error" | string;
  text: string;
  meta?: string;
};

/** 等人闸门；旧 clarify_hitl/advise 仅兼容本机快照 */
export type ClarifyGate = "hitl" | "clarify_hitl" | "advise" | null;

export type SessionState = {
  health: Health | null;
  agentRole: string;
  displayName: string;
  prefs: SessionPrefs;
  threadId: string;
  phase: string | null;
  feed: FeedItem[];
  needApproval: boolean;
  needClarify: boolean;
  clarifyGate: ClarifyGate;
  /** 运行中用户输入的缓存；闸门到来后优先 say */
  pendingAsks: string[];
  planSummary: string;
  running: boolean;
  started: boolean;
  /** 本局累计（SSE usage 累加） */
  promptTokens: number;
  completionTokens: number;
};

let state: SessionState = {
  health: null,
  agentRole: "scene_perf",
  displayName: "",
  prefs: defaultPrefs(),
  threadId: "",
  phase: null,
  feed: [],
  needApproval: false,
  needClarify: false,
  clarifyGate: null,
  pendingAsks: [],
  planSummary: "",
  running: false,
  started: false,
  promptTokens: 0,
  completionTokens: 0,
};

type Listener = () => void;
const listeners = new Set<Listener>();

export function getSession(): SessionState {
  return state;
}

export function subscribe(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  listeners.forEach((fn) => fn());
}

function patch(partial: Partial<SessionState>) {
  state = { ...state, ...partial };
  emit();
}

export function setHealth(health: Health | null) {
  patch({ health });
}

export function setAgentRole(agentRole: string) {
  patch({ agentRole });
}

export function setDisplayName(displayName: string) {
  patch({ displayName });
}

export function setPrefs(prefs: SessionPrefs) {
  patch({ prefs });
}

/** 选助手后预填不重名默认名（改扮演时重算）；顺带清掉已绑定 thread。 */
export function prepareAssistantDraft(agentRole: string, namePrefix: string) {
  patch({
    agentRole,
    displayName: suggestDisplayName(agentRole, namePrefix),
    threadId: "",
    started: false,
    running: false,
    feed: [],
    phase: null,
    pendingAsks: [],
    clarifyGate: null,
    promptTokens: 0,
    completionTokens: 0,
  });
}

/** 选助手确认后：只分配 thread，尚未进对话页 → 不写 localStorage。 */
export function beginNewSession(): void {
  const threadId = `t_${Date.now()}`;
  patch({
    threadId,
    phase: null,
    feed: [],
    needApproval: false,
    needClarify: false,
    clarifyGate: null,
    pendingAsks: [],
    planSummary: "",
    running: false,
    started: false,
    prefs: defaultPrefs(),
    promptTokens: 0,
    completionTokens: 0,
  });
}

/** 打开最近会话：直达对话页；恢复 prefs + 本机事件流快照 + 闸门（稍后仍以 /chat/gate 为准）。 */
export function openSavedSession(rec: SavedSession) {
  const rawGate = rec.clarifyGate;
  const gate: ClarifyGate =
    rawGate === "hitl" ||
    rawGate === "advise" ||
    rawGate === "clarify_hitl"
      ? rawGate
      : rec.needClarify
        ? "hitl"
        : null;
  patch({
    threadId: rec.threadId,
    displayName: rec.displayName,
    agentRole: rec.agentRole,
    phase: rec.phase ?? null,
    feed: Array.isArray(rec.feed) ? rec.feed : [],
    needApproval: !!rec.needApproval,
    needClarify: !!rec.needClarify,
    clarifyGate: gate,
    pendingAsks: [],
    planSummary: rec.planSummary || "",
    running: false,
    started: !!rec.started || (Array.isArray(rec.feed) && rec.feed.length > 0),
    prefs: rec.prefs ? { ...defaultPrefs(), ...rec.prefs } : defaultPrefs(),
    promptTokens: Number(rec.promptTokens) || 0,
    completionTokens: Number(rec.completionTokens) || 0,
  });
}

/** 进入/更新对话页时登记最近会话（含口播 feed，否则回工作台再进是空的）。 */
export function persistCurrentSessionMeta(mcpUrl?: string) {
  if (!state.threadId || !state.displayName.trim()) return;
  upsertSession({
    threadId: state.threadId,
    displayName: state.displayName.trim(),
    agentRole: state.agentRole,
    updatedAt: Date.now(),
    mcpUrl: mcpUrl || state.health?.mcp_url || undefined,
    prefs: state.prefs,
    feed: state.feed,
    phase: state.phase,
    started: state.started,
    needClarify: state.needClarify,
    clarifyGate: state.clarifyGate,
    needApproval: state.needApproval,
    planSummary: state.planSummary,
    promptTokens: state.promptTokens,
    completionTokens: state.completionTokens,
  });
}

export function roleLabelKey(agentRole: string): string {
  return ROLE_META[agentRole]?.labelKey || "wizard.assistant";
}

export function appendFeed(kind: string, text: string, meta?: string) {
  const item: FeedItem = {
    id: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    kind,
    text,
    meta,
  };
  patch({ feed: [...state.feed, item] });
}

function appendToken(chunk: string) {
  // 合并后再剥：伪工具标记常跨多个 SSE chunk，单包 strip 挡不住
  if (!chunk) return;
  const feed = state.feed;
  const last = feed[feed.length - 1];
  if (last?.kind === "token") {
    const merged = stripToolMarkup(last.text + chunk);
    if (!merged) {
      // 整段都被剥掉则去掉这条空口播
      patch({ feed: feed.slice(0, -1) });
      return;
    }
    if (merged === last.text) return;
    const next = feed.slice();
    next[next.length - 1] = { ...last, text: merged };
    patch({ feed: next });
    return;
  }
  const cleaned = stripToolMarkup(chunk);
  if (!cleaned) return;
  appendFeed("token", cleaned);
}

/** 工具名缩短展示：unity_perf_capture_frame → capture_frame */
function shortToolName(name: string): string {
  return name
    .replace(/^unity_perf_/, "")
    .replace(/^unity_/, "")
    .replace(/^mcp__/, "");
}

/**
 * 连续工具调用并成一条，否则感知 checklist 会刷十来行。
 * meta 存 | 分隔全名；text 为短名串联。
 */
function mergeToolEvent(name: string, phase: "call" | "result") {
  const feed = state.feed;
  const last = feed[feed.length - 1];
  const names =
    last?.kind === "tools" && last.meta
      ? last.meta.split("|").filter(Boolean)
      : [];

  if (last?.kind === "tools") {
    if (phase === "call" && names[names.length - 1] !== name) {
      names.push(name);
    } else if (phase === "result" && !names.includes(name)) {
      names.push(name);
    }
    const next = feed.slice();
    next[next.length - 1] = {
      ...last,
      text: names.map(shortToolName).join(" · "),
      meta: names.join("|"),
    };
    patch({ feed: next });
    return;
  }

  appendFeed("tools", shortToolName(name), name);
}

/** 运行中缓存输入；闸门到了再一次性 say。 */
export function enqueuePendingAsk(text: string) {
  const t = text.trim();
  if (!t) return;
  patch({ pendingAsks: [...state.pendingAsks, t] });
  appendFeed("user", t, "queued");
}

/** 取出并清空缓存；多条用空行拼成一次发送。 */
export function consumePendingAsks(): string {
  const texts = state.pendingAsks;
  if (!texts.length) return "";
  patch({ pendingAsks: [] });
  return texts.join("\n\n");
}

export function applyStreamEvent(ev: Record<string, unknown>) {
  const type = String(ev.type || "");
  if (type === "phase") {
    const name = String(ev.name || ev.title || "");
    const prev = state.phase;
    patch({ phase: name });
    clientLog(`phase ${name}`);
    // 同站重复 phase 省略，避免刷屏
    if (name && name === prev) {
      schedulePersistFeed();
      return;
    }
    appendFeed("phase", String(ev.title || ev.name || ""));
  } else if (type === "token") {
    appendToken(String(ev.content || ""));
  } else if (type === "observation") {
    // 观测数字进 analyze 口播，前端不挂独立行
  } else if (type === "tool_call") {
    mergeToolEvent(String(ev.name || "tool"), "call");
  } else if (type === "tool_result") {
    mergeToolEvent(String(ev.name || "tool"), "result");
  } else if (type === "need_approval") {
    patch({
      needApproval: true,
      needClarify: false,
      clarifyGate: null,
      planSummary: String(ev.plan_summary || ""),
      running: false,
    });
    clientLog("need_approval", ev.plan_summary);
    appendFeed("hitl", String(ev.plan_summary || "需要批准"));
  } else if (type === "need_clarify") {
    const gateRaw = String(ev.gate || "hitl");
    const clarifyGate: ClarifyGate =
      gateRaw === "advise"
        ? "advise"
        : gateRaw === "clarify_hitl"
          ? "clarify_hitl"
          : "hitl";
    patch({
      needClarify: true,
      needApproval: false,
      clarifyGate,
      running: false,
    });
    clientLog(`need_input gate=${clarifyGate}`);
  } else if (type === "usage") {
    const p = Number(ev.prompt_tokens) || 0;
    const c = Number(ev.completion_tokens) || 0;
    if (p || c) {
      patch({
        promptTokens: state.promptTokens + p,
        completionTokens: state.completionTokens + c,
      });
    }
  } else if (type === "run_end") {
    // 正常收尾：无闸门。勿清 pending——若有缓存留给下次开局前手动丢弃
    clientLog("run_end");
    patch({
      running: false,
      needApproval: false,
      needClarify: false,
      clarifyGate: null,
    });
  } else if (type === "error") {
    clientLog("error", ev.content);
    appendFeed("error", String(ev.content || "错误"));
    patch({ running: false });
  } else if (type === "run_start") {
    clientLog(`run_start thread=${state.threadId}`);
    patch({
      running: true,
      started: true,
      needApproval: false,
      needClarify: false,
      clarifyGate: null,
    });
  } else if (type === "user") {
    appendFeed("user", String(ev.content || ""));
  }
  // 口播进本地快照，回工作台再进才能看见；token 很密，做短防抖
  schedulePersistFeed();
}

let _persistTimer: ReturnType<typeof setTimeout> | null = null;
function schedulePersistFeed() {
  if (_persistTimer) clearTimeout(_persistTimer);
  _persistTimer = setTimeout(() => {
    _persistTimer = null;
    persistCurrentSessionMeta();
  }, 400);
}

export function setRunning(running: boolean) {
  patch({ running });
}

export function clearApproval() {
  patch({ needApproval: false });
}

export function clearClarify() {
  patch({ needClarify: false, clarifyGate: null });
}

/** 按服务端 checkpoint 恢复闸门（打开最近会话用）。 */
export function applyThreadGate(g: {
  need_clarify: boolean;
  need_approval: boolean;
  gate: string | null;
  plan_summary?: string;
  phase?: string | null;
}) {
  const clarifyGate: ClarifyGate =
    g.gate === "advise"
      ? "advise"
      : g.gate === "clarify_hitl"
        ? "clarify_hitl"
        : g.gate === "hitl" || g.need_clarify
          ? "hitl"
          : null;
  patch({
    needClarify: !!g.need_clarify,
    needApproval: !!g.need_approval,
    clarifyGate: g.need_clarify ? clarifyGate : null,
    planSummary: g.plan_summary || state.planSummary,
    phase: g.phase ?? state.phase,
    running: false,
  });
  persistCurrentSessionMeta();
}
