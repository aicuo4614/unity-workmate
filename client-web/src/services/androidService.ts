/** 侧栏：先扫设备，再按所选序列号连接；Logcat SSE / Perfetto 也走这里。 */
import {
  API_BASE,
  type AndroidPanel,
  type AndroidPerfettoHistoryItem,
  type AndroidPerfettoResult,
  type AndroidProfilerConvertResult,
  type AndroidProfilerHistoryItem,
  type AndroidProfilerOpenResult,
  type AndroidProfilerResult,
  type ProfilerDetailData,
  type ProfilerCallTree,
  type AndroidScan,
} from "./types";

export type LogcatEntry = {
  date: string;
  package: string;
  pid: number;
  tid: number;
  priority: number;
  tag: string;
  message: string;
};

export type LogcatStreamEvent =
  | { type: "status"; ok: boolean; serial?: string; error?: string }
  | { type: "entries"; items: LogcatEntry[] }
  | { type: "error"; content: string };

/** 后端未就绪 / Vite 502。UI 须用 androidErrorText 转成文案，禁止直接展示该码。 */
export const ANDROID_SERVER_DOWN = "SERVER_DOWN";
/** 请求超时（服务可能在 reload / 忙）。 */
export const ANDROID_TIMEOUT = "TIMEOUT";

function classifyAndroidFetchError(err: unknown, status?: number): string | null {
  if (status === 502 || status === 503 || status === 504) return ANDROID_SERVER_DOWN;
  if (err instanceof DOMException && err.name === "TimeoutError") return ANDROID_TIMEOUT;
  // AbortSignal.timeout 在部分环境表现为 AbortError
  if (err instanceof DOMException && err.name === "AbortError") return ANDROID_TIMEOUT;
  if (err instanceof TypeError) return ANDROID_SERVER_DOWN; // Failed to fetch
  return null;
}

/** 把 SERVICE 错误码翻成界面文案；其它 Error.message 原样返回。 */
export function androidErrorText(
  e: unknown,
  t: (key: string) => string,
  fallbackKey: string,
): string {
  const msg = e instanceof Error ? e.message : "";
  if (msg === ANDROID_SERVER_DOWN) return t("device.serverDownShort");
  if (msg === ANDROID_TIMEOUT) return t("device.requestTimeout");
  if (msg) return msg;
  return t(fallbackKey);
}

async function callJson<T>(
  path: string,
  init: RequestInit & { timeoutMs: number },
): Promise<T> {
  const { timeoutMs, ...rest } = init;
  try {
    const resp = await fetch(`${API_BASE}${path}`, {
      ...rest,
      signal: AbortSignal.timeout(timeoutMs),
      headers: {
        "Content-Type": "application/json",
        ...(rest.headers || {}),
      },
    });
    if (!resp.ok) {
      const code = classifyAndroidFetchError(null, resp.status);
      if (code) throw new Error(code);
      const body = await resp.json().catch(() => null);
      throw new Error(typeof body?.detail === "string" ? body.detail : `android HTTP ${resp.status}`);
    }
    return resp.json();
  } catch (e) {
    if (
      e instanceof Error &&
      (e.message === ANDROID_SERVER_DOWN || e.message === ANDROID_TIMEOUT)
    ) {
      throw e;
    }
    const code = classifyAndroidFetchError(e);
    if (code) throw new Error(code);
    throw e;
  }
}

export function scanAndroidDevices(): Promise<AndroidScan> {
  return callJson("/mcp/android/devices", { method: "GET", timeoutMs: 12000 });
}

export function fetchAndroidPanel(): Promise<Omit<AndroidPanel, "loading">> {
  return callJson("/mcp/android/status", { method: "GET", timeoutMs: 12000 });
}

export function connectAndroidPanel(
  serial: string,
): Promise<Omit<AndroidPanel, "loading">> {
  return callJson("/mcp/android/connect", {
    method: "POST",
    timeoutMs: 60000,
    body: JSON.stringify({ serial }),
  });
}

export function disconnectAndroidPanel(): Promise<Omit<AndroidPanel, "loading">> {
  return callJson("/mcp/android/disconnect", { method: "POST", timeoutMs: 15000 });
}

export function clearAndroidLogcat(serial: string): Promise<{ ok: boolean; error: string }> {
  return callJson("/mcp/android/logcat/clear", {
    method: "POST",
    timeoutMs: 12000,
    body: JSON.stringify({ serial }),
  });
}

export function captureAndroidPerfetto(opts: {
  serial?: string;
  duration_sec?: number;
  package?: string;
  thread_id?: string;
}): Promise<AndroidPerfettoResult> {
  // 录制本身要 duration+余量；给足超时避免前端先断
  const duration = Math.max(3, Math.min(30, opts.duration_sec ?? 5));
  return callJson("/mcp/android/perfetto/capture", {
    method: "POST",
    timeoutMs: (duration + 90) * 1000,
    body: JSON.stringify({
      serial: opts.serial || "",
      duration_sec: duration,
      package: opts.package || "",
      thread_id: opts.thread_id || "",
    }),
  });
}

export function fetchAndroidPerfettoLatest(threadId?: string): Promise<AndroidPerfettoResult> {
  const q = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : "";
  return callJson(`/mcp/android/perfetto/latest${q}`, { method: "GET", timeoutMs: 12000 });
}

export function fetchAndroidPerfettoHistory(
  limit = 40,
  threadId = "",
): Promise<{ ok: boolean; error: string; items: AndroidPerfettoHistoryItem[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (threadId) params.set("thread_id", threadId);
  return callJson(`/mcp/android/perfetto/history?${params}`, {
    method: "GET",
    timeoutMs: 15000,
  });
}

export function loadAndroidPerfettoHistory(
  id: string,
  threadId = "",
): Promise<AndroidPerfettoResult> {
  return callJson("/mcp/android/perfetto/load", {
    method: "POST",
    timeoutMs: 180000,
    body: JSON.stringify({ id, thread_id: threadId }),
  });
}

/** trace 下载地址；可传历史文件名。 */
export function androidPerfettoDownloadUrl(name?: string): string {
  const q = name ? `?name=${encodeURIComponent(name)}` : "";
  return `${API_BASE}/mcp/android/perfetto/download${q}`;
}

export function importAndroidProfiler(file: File, threadId: string): Promise<AndroidProfilerResult> {
  const q = new URLSearchParams({ name: file.name, thread_id: threadId });
  return callJson(`/mcp/android/profiler/import?${q}`, {
    method: "POST", body: file, timeoutMs: 30 * 60 * 1000,
    headers: { "Content-Type": "application/octet-stream" },
  });
}

export function startAndroidProfiler(opts?: {
  serial?: string;
  package?: string;
  thread_id?: string;
}): Promise<AndroidProfilerResult> {
  return callJson("/mcp/android/profiler/start", {
    method: "POST",
    timeoutMs: 60000,
    body: JSON.stringify({
      serial: opts?.serial || "",
      package: opts?.package || "",
      thread_id: opts?.thread_id || "",
    }),
  });
}

export function stopAndroidProfiler(): Promise<AndroidProfilerResult> {
  // 服务端先停插件就返回，大文件 pull 在后台。这里不用再干等整份 .raw。
  return callJson("/mcp/android/profiler/stop", {
    method: "POST",
    timeoutMs: 30000,
    body: "{}",
  });
}

export function fetchAndroidProfilerLatest(threadId?: string): Promise<AndroidProfilerResult> {
  const q = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : "";
  return callJson(`/mcp/android/profiler/latest${q}`, { method: "GET", timeoutMs: 12000 });
}

export function fetchAndroidProfilerHistory(
  limit = 40,
  threadId = "",
): Promise<{ ok: boolean; error: string; items: AndroidProfilerHistoryItem[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (threadId) params.set("thread_id", threadId);
  return callJson(`/mcp/android/profiler/history?${params}`, {
    method: "GET",
    timeoutMs: 15000,
  });
}

export function loadAndroidProfilerHistory(
  id: string,
  threadId = "",
): Promise<AndroidProfilerResult> {
  return callJson("/mcp/android/profiler/load", {
    method: "POST",
    timeoutMs: 60000,
    body: JSON.stringify({ id, thread_id: threadId }),
  });
}

export function androidProfilerShotUrl(name: string, file: string): string {
  const q = new URLSearchParams({ name, file });
  return `${API_BASE}/mcp/android/profiler/shot?${q}`;
}

export function androidProfilerDownloadUrl(name?: string): string {
  const q = name ? `?name=${encodeURIComponent(name)}` : "";
  return `${API_BASE}/mcp/android/profiler/download${q}`;
}

export function fetchAndroidProfilerDetail(
  name: string,
  threadId = "",
): Promise<ProfilerDetailData> {
  const q = new URLSearchParams({ name });
  if (threadId) q.set("thread_id", threadId);
  // 全帧 JSON，比 latest 大，单独拉一次
  return callJson<ProfilerDetailData>(`/mcp/android/profiler/detaildata?${q}`, {
    method: "GET",
    timeoutMs: 120000,
  });
}

export function openAndroidProfilerInEditor(
  id = "",
  threadId = "",
  frame = -1,
): Promise<AndroidProfilerOpenResult> {
  return callJson<AndroidProfilerOpenResult>("/mcp/android/profiler/open-editor", {
    method: "POST",
    timeoutMs: 30000,
    body: JSON.stringify({ id, thread_id: threadId, frame }),
  });
}

export function revealAndroidProfilerFolder(
  id = "",
  threadId = "",
): Promise<{ ok: boolean; error: string; path: string }> {
  return callJson("/mcp/android/profiler/reveal", {
    method: "POST",
    timeoutMs: 15000,
    body: JSON.stringify({ id, thread_id: threadId }),
  });
}

export function convertAndroidProfilerAnalysis(
  id = "",
  threadId = "",
  timeoutSec = 600,
): Promise<AndroidProfilerConvertResult> {
  // 等 Unity Load/Save/抽摘要；首次旁路工程编译可能很久
  return callJson<AndroidProfilerConvertResult>("/mcp/android/profiler/convert", {
    method: "POST",
    timeoutMs: Math.max(120000, Math.floor(timeoutSec * 1000) + 30000),
    body: JSON.stringify({ id, thread_id: threadId, timeout_sec: timeoutSec }),
  });
}

/** Logcat SSE：fetch 流，便于 AbortSignal 停推流。 */
export async function streamAndroidLogcat(opts: {
  serial: string;
  onEvent: (ev: LogcatStreamEvent) => void;
  signal?: AbortSignal;
}): Promise<void> {
  const q = new URLSearchParams({ serial: opts.serial });
  const resp = await fetch(`${API_BASE}/mcp/android/logcat/stream?${q}`, {
    method: "GET",
    signal: opts.signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`logcat HTTP ${resp.status}`);
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
      if (!data || data === "[DONE]") continue;
      try {
        opts.onEvent(JSON.parse(data) as LogcatStreamEvent);
      } catch {
        /* 忽略半包 */
      }
    }
  }
}


export function selectAndroidProfilerFrames(id: string, threadId: string, frames: number[]) {
  return callJson<{ ok: boolean; frames: number[] }>("/mcp/android/profiler/custom-frames", {
    method: "POST",
    timeoutMs: 120000,
    body: JSON.stringify({ id, thread_id: threadId, frames }),
  });
}

/** 单帧调用树仅在展开时下载，同帧各排行榜共用。 */
export function fetchAndroidProfilerCallTree(name: string, threadId: string, frame: number): Promise<ProfilerCallTree> {
  const q = new URLSearchParams({ name, thread_id: threadId, frame: String(frame) });
  return callJson<ProfilerCallTree>(`/mcp/android/profiler/calltree?${q}`, { method: "GET", timeoutMs: 120000 });
}
