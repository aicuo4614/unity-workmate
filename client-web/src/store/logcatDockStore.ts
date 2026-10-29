/**
 * 真机底栏开合/高度/当前 Tab（Logcat | Perfetto | Profiler）。
 * 放在 store：顶栏按钮与底栏本体共享，跨次进入记住高度与上次 Tab。
 */
const STORAGE_KEY = "ailearning.deviceDock.v1";
const LEGACY_KEY = "ailearning.logcatDock.v1";
const DEFAULT_HEIGHT = 280;
const TITLE_HEIGHT = 32;

export type DeviceDockTab = "logcat" | "perfetto" | "profiler" | "renderdoc";

function normalizeTab(raw: unknown): DeviceDockTab {
  if (raw === "perfetto" || raw === "profiler" || raw === "renderdoc") return raw;
  return "logcat";
}

type DockState = {
  open: boolean;
  /** 展开高度（px）；收起时仍保留，用于再展开 */
  height: number;
  collapsed: boolean;
  tab: DeviceDockTab;
  /** 覆盖 session-mid；不落盘，刷新后回到普通底栏 */
  fullscreen: boolean;
};

let state: DockState = load();
const listeners = new Set<() => void>();

function load(): DockState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY) || localStorage.getItem(LEGACY_KEY);
    if (!raw) {
      return {
        open: false,
        height: DEFAULT_HEIGHT,
        collapsed: false,
        tab: "logcat",
        fullscreen: false,
      };
    }
    const parsed = JSON.parse(raw) as Partial<DockState>;
    return {
      open: !!parsed.open,
      height: Math.max(TITLE_HEIGHT, Number(parsed.height) || DEFAULT_HEIGHT),
      collapsed: !!parsed.collapsed,
      tab: normalizeTab(parsed.tab),
      fullscreen: false,
    };
  } catch {
    return {
      open: false,
      height: DEFAULT_HEIGHT,
      collapsed: false,
      tab: "logcat",
      fullscreen: false,
    };
  }
}

function persist() {
  try {
    // fullscreen 是临时布局，不写 localStorage，避免刷新后整页被盖住
    const { fullscreen: _fs, ...rest } = state;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(rest));
  } catch {
    /* ignore quota */
  }
}

function emit() {
  listeners.forEach((fn) => fn());
}

export function getLogcatDock(): DockState {
  return state;
}

export function getLogcatTitleHeight(): number {
  return TITLE_HEIGHT;
}

export function getLogcatDefaultHeight(): number {
  return DEFAULT_HEIGHT;
}

/** Logcat 推流：底栏打开、未收起、且当前就是 Logcat Tab */
export function isLogcatDockActive(): boolean {
  return state.open && !state.collapsed && state.tab === "logcat";
}

export function setLogcatDockOpen(open: boolean) {
  state = { ...state, open, fullscreen: open ? state.fullscreen : false };
  if (open && state.collapsed) {
    state = { ...state, collapsed: false };
  }
  persist();
  emit();
}

export function toggleLogcatDock() {
  openDeviceDockTab("logcat");
}

/** 顶栏按钮：同 Tab 再点则关；否则打开并切到该 Tab */
export function openDeviceDockTab(tab: DeviceDockTab) {
  if (state.open && !state.collapsed && state.tab === tab && !state.fullscreen) {
    state = { ...state, open: false, fullscreen: false };
  } else {
    state = { ...state, open: true, collapsed: false, tab };
  }
  persist();
  emit();
}

export function setDeviceDockTab(tab: DeviceDockTab) {
  state = { ...state, tab };
  if (!state.open) {
    state = { ...state, open: true, collapsed: false };
  }
  persist();
  emit();
}

export function setLogcatDockCollapsed(collapsed: boolean) {
  // 全屏时收起没有意义；收起同时退出全屏，回到标题条
  state = {
    ...state,
    collapsed,
    fullscreen: collapsed ? false : state.fullscreen,
  };
  persist();
  emit();
}

export function setDeviceDockFullscreen(fullscreen: boolean) {
  if (fullscreen) {
    state = { ...state, open: true, collapsed: false, fullscreen: true };
  } else {
    state = { ...state, fullscreen: false };
  }
  persist();
  emit();
}

/** 右键 Tab：切到该 Tab 并进入/退出全屏 */
export function toggleDeviceDockFullscreen(tab: DeviceDockTab) {
  if (state.open && state.fullscreen && state.tab === tab) {
    state = { ...state, fullscreen: false };
  } else {
    state = { ...state, open: true, collapsed: false, tab, fullscreen: true };
  }
  persist();
  emit();
}

export function setLogcatDockHeight(height: number) {
  state = { ...state, height: Math.max(TITLE_HEIGHT, Math.round(height)) };
  persist();
  emit();
}

/** 拖拽松手：靠近标题条/默认高度时软吸附 */
export function snapLogcatDockHeight(raw: number, bodyHeight: number) {
  const max = Math.max(TITLE_HEIGHT, Math.floor(bodyHeight * 0.6));
  let h = Math.min(max, Math.max(TITLE_HEIGHT, Math.round(raw)));
  if (h <= TITLE_HEIGHT + 28) {
    state = {
      ...state,
      height: state.height > TITLE_HEIGHT ? state.height : DEFAULT_HEIGHT,
      collapsed: true,
    };
  } else {
    if (Math.abs(h - DEFAULT_HEIGHT) < 28) h = DEFAULT_HEIGHT;
    state = { ...state, height: h, collapsed: false };
  }
  persist();
  emit();
}

export function subscribeLogcatDock(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
