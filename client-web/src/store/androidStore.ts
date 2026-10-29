/**
 * 真机侧栏状态。
 * 扫描 ≠ 连接：扫描只列设备；选好后再连接才拉起 Android MCP 并轮询。
 */
import {
  ANDROID_SERVER_DOWN,
  connectAndroidPanel,
  disconnectAndroidPanel,
  fetchAndroidPanel,
  scanAndroidDevices,
} from "../services/androidService";
import type { AndroidDevice, AndroidPanel } from "../services/types";

function asPanelError(e: unknown): string {
  if (e instanceof Error && e.message === ANDROID_SERVER_DOWN) return ANDROID_SERVER_DOWN;
  return e instanceof Error ? e.message : String(e);
}

const POLL_MS = 3000;

let status: AndroidPanel = {
  ok: false,
  error: "",
  devices: [],
  foreground: "",
  serial: "",
  loading: false,
};
/** 下拉框候选；未连接时用这份列表 */
let candidates: AndroidDevice[] = [];
let selectedSerial = "";
let scanning = false;
let armed = false;
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let inflight = false;

function emit() {
  listeners.forEach((fn) => fn());
}

function stopPolling() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
}

function startPolling() {
  if (timer) return;
  timer = setInterval(() => void pollStatus(), POLL_MS);
}

async function pollStatus() {
  if (inflight || !armed) return;
  inflight = true;
  try {
    const data = await fetchAndroidPanel();
    status = { ...data, loading: false };
    if (data.serial) selectedSerial = data.serial;
  } catch (e) {
    status = {
      ...status,
      ok: false,
      error: asPanelError(e),
      loading: false,
    };
  } finally {
    inflight = false;
    emit();
  }
}

export function getAndroid(): AndroidPanel {
  return status;
}

export function getAndroidCandidates(): AndroidDevice[] {
  return candidates;
}

export function getSelectedSerial(): string {
  return selectedSerial;
}

export function isAndroidScanning(): boolean {
  return scanning;
}

export function setSelectedSerial(serial: string) {
  selectedSerial = serial;
  emit();
}

export async function scanAndroid() {
  if (scanning) return;
  scanning = true;
  emit();
  try {
    const data = await scanAndroidDevices();
    candidates = data.devices || [];
    if (!data.ok) {
      status = { ...status, error: data.error || "扫描失败", loading: false };
    } else {
      status = { ...status, error: "", loading: false };
      const online = candidates.filter((item) => item.state === "device");
      if (!selectedSerial && online.length === 1) {
        selectedSerial = online[0].serial;
      }
      if (selectedSerial && !candidates.some((item) => item.serial === selectedSerial)) {
        selectedSerial = online[0]?.serial || "";
      }
    }
  } catch (e) {
    candidates = [];
    status = {
      ...status,
      error: asPanelError(e),
      loading: false,
    };
  } finally {
    scanning = false;
    emit();
  }
}

export function connectAndroid() {
  const serial = selectedSerial.trim();
  if (!serial || inflight) return;
  armed = true;
  inflight = true;
  status = { ...status, loading: true, error: "" };
  emit();
  void connectAndroidPanel(serial)
    .then((data) => {
      status = { ...data, loading: false };
      if (data.serial) selectedSerial = data.serial;
      startPolling();
    })
    .catch((e) => {
      armed = false;
      status = {
        ok: false,
        error: asPanelError(e),
        devices: [],
        foreground: "",
        serial: "",
        loading: false,
      };
    })
    .finally(() => {
      inflight = false;
      emit();
    });
}

export function disconnectAndroid() {
  if (inflight) return;
  armed = false;
  stopPolling();
  inflight = true;
  status = { ...status, loading: true, error: "" };
  emit();
  void disconnectAndroidPanel()
    .then((data) => {
      status = { ...data, loading: false };
    })
    .catch((e) => {
      status = {
        ok: false,
        error: asPanelError(e),
        devices: [],
        foreground: "",
        serial: "",
        loading: false,
      };
    })
    .finally(() => {
      inflight = false;
      emit();
    });
}

export function subscribeAndroid(listener: () => void): () => void {
  listeners.add(listener);
  const wake = armed && !timer && !inflight;
  if (armed) startPolling();
  if (wake) void pollStatus();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) stopPolling();
  };
}
