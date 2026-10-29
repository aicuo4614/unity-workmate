/**
 * Logcat 缓冲。数据不跟面板走：切 Tab、收起底栏只卸界面。
 * 推流跟已连接设备走，不跟当前 Tab。界面只订阅读取可见行。
 */
import {
  clearAndroidLogcat,
  streamAndroidLogcat,
  type LogcatEntry,
} from "../services/androidService";

/** 纯数据环形缓冲。DOM 只画可见行，所以可以比以前的 2500 条 DOM 大很多。 */
const MAX_LINES = 50000;
const DROP_CHUNK = 4000;

export type LogLine = {
  id: number;
  date: string;
  priority: number;
  tag: string;
  package: string;
  message: string;
};

export type LogcatQuery = {
  priority: number;
  tag: string;
  package: string;
  text: string;
};

export type LogcatStatus = {
  serial: string;
  running: boolean;
  error: string;
};

let lines: LogLine[] = [];
let view: LogLine[] = [];
let nextId = 1;
let query: LogcatQuery = { priority: 2, tag: "", package: "", text: "" };
let queryKey = "2\n\n\n";
let status: LogcatStatus = { serial: "", running: false, error: "" };
let ac: AbortController | null = null;
let startGen = 0;
/** 刚从缓冲头部丢掉的行数，界面用来修正滚动位置 */
let pendingTrim = 0;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((fn) => fn());
}

function queryKeyOf(q: LogcatQuery): string {
  return `${q.priority}\n${q.tag.trim().toLowerCase()}\n${q.package.trim().toLowerCase()}\n${q.text.trim().toLowerCase()}`;
}

function match(line: LogLine, tag: string, pkg: string, text: string, priority: number): boolean {
  if (line.priority < priority) return false;
  if (tag && !line.tag.toLowerCase().includes(tag)) return false;
  if (pkg && !line.package.toLowerCase().includes(pkg)) return false;
  if (!text) return true;
  return line.message.toLowerCase().includes(text) || line.tag.toLowerCase().includes(text);
}

function rebuildView() {
  const tag = query.tag.trim().toLowerCase();
  const pkg = query.package.trim().toLowerCase();
  const text = query.text.trim().toLowerCase();
  const priority = query.priority;
  const next: LogLine[] = [];
  for (let i = 0; i < lines.length; i++) {
    if (match(lines[i], tag, pkg, text, priority)) next.push(lines[i]);
  }
  view = next;
}

function trimFront() {
  if (lines.length <= MAX_LINES) return;
  const drop = Math.min(lines.length, lines.length - MAX_LINES + DROP_CHUNK);
  const lastDropped = lines[drop - 1]?.id ?? 0;
  lines.splice(0, drop);
  if (view.length && view[0].id <= lastDropped) {
    let lo = 0;
    let hi = view.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (view[mid].id <= lastDropped) lo = mid + 1;
      else hi = mid;
    }
    view.splice(0, lo);
    pendingTrim += lo;
  }
}

function pushEntry(entry: LogcatEntry) {
  const tag = query.tag.trim().toLowerCase();
  const pkg = query.package.trim().toLowerCase();
  const text = query.text.trim().toLowerCase();
  const priority = query.priority;
  const parts = String(entry.message ?? "").split("\n");
  if (!parts.length) parts.push("");
  for (let i = 0; i < parts.length; i++) {
    const line: LogLine = {
      id: nextId++,
      date: entry.date,
      priority: entry.priority,
      tag: entry.tag,
      package: entry.package,
      message: parts[i],
    };
    lines.push(line);
    if (match(line, tag, pkg, text, priority)) view.push(line);
  }
  trimFront();
}

export function subscribeLogcat(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getLogcatView(): LogLine[] {
  return view;
}

export function getLogcatStatus(): LogcatStatus {
  return status;
}

/** 界面读完即清零，避免重复减滚动条。 */
export function takeLogcatTrim(): number {
  const n = pendingTrim;
  pendingTrim = 0;
  return n;
}

export function setLogcatQuery(next: LogcatQuery) {
  const key = queryKeyOf(next);
  query = next;
  if (key === queryKey) return;
  queryKey = key;
  rebuildView();
  emit();
}

export function clearLogcatBuffer() {
  lines = [];
  view = [];
  pendingTrim = 0;
  emit();
}

export async function clearLogcatDevice(serial: string): Promise<string> {
  clearLogcatBuffer();
  if (!serial) return "";
  try {
    const res = await clearAndroidLogcat(serial);
    return res.ok ? "" : res.error || "";
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
}

export function stopLogcat() {
  startGen += 1;
  ac?.abort();
  ac = null;
  if (status.running) {
    status = { ...status, running: false };
    emit();
  }
}

/** 同一台已在推流则不动。换设备才清缓冲。用户点停止后不会被这里自动拉起。 */
export function ensureLogcat(serial: string) {
  const next = serial.trim();
  if (!next) {
    stopLogcat();
    return;
  }
  if (status.running && status.serial === next) return;
  void startLogcat(next, status.serial !== "" && status.serial !== next);
}

/** 点「开始」：接着收，不清已有行。 */
export function restartLogcat(serial: string) {
  const next = serial.trim();
  if (!next) return;
  void startLogcat(next, false);
}

async function startLogcat(serial: string, reset: boolean) {
  startGen += 1;
  const gen = startGen;
  ac?.abort();
  const ctrl = new AbortController();
  ac = ctrl;
  if (reset) {
    lines = [];
    view = [];
    pendingTrim = 0;
  }
  status = { serial, running: true, error: "" };
  emit();
  try {
    await streamAndroidLogcat({
      serial,
      signal: ctrl.signal,
      onEvent: (ev) => {
        if (gen !== startGen) return;
        if (ev.type === "status") {
          status = { serial, running: true, error: "" };
          emit();
          return;
        }
        if (ev.type === "error") {
          status = { ...status, error: String(ev.content || "") };
          emit();
          return;
        }
        if (ev.type === "entries" && Array.isArray(ev.items)) {
          for (let i = 0; i < ev.items.length; i++) pushEntry(ev.items[i]);
          emit();
        }
      },
    });
  } catch (e) {
    if ((e as Error)?.name === "AbortError") return;
    if (gen !== startGen) return;
    status = {
      ...status,
      running: false,
      error: e instanceof Error ? e.message : String(e),
    };
    emit();
    return;
  }
  if (gen !== startGen) return;
  status = { ...status, running: false };
  emit();
}
