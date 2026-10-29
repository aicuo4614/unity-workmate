/**
 * 真机底栏：Logcat / Perfetto / Profiler 共用壳，顶栏或标题 Tab 切换。
 * Tab 右键：全屏覆盖对话区，或关闭底栏。
 * Logcat 推流跟设备连接走，不跟当前 Tab；切走只卸界面。
 */
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { useTranslation } from "react-i18next";
import { Button, Dropdown } from "antd";
import type { MenuProps } from "antd";
import { getAndroid, subscribeAndroid } from "../store/androidStore";
import { ensureLogcat, stopLogcat } from "../store/logcatStore";
import {
  getLogcatDock,
  getLogcatTitleHeight,
  isLogcatDockActive,
  setDeviceDockFullscreen,
  setDeviceDockTab,
  setLogcatDockCollapsed,
  setLogcatDockOpen,
  snapLogcatDockHeight,
  subscribeLogcatDock,
  toggleDeviceDockFullscreen,
  type DeviceDockTab,
} from "../store/logcatDockStore";
import { getSession, subscribe as subscribeSession } from "../store/sessionStore";
import { LogcatPanel } from "./LogcatPanel";
import { PerfettoPanel } from "./PerfettoPanel";
import { ProfilerPanel } from "./ProfilerPanel";
import { RenderDocPanel } from "./RenderDocPanel";

function useDock() {
  const [, tick] = useState(0);
  useEffect(() => subscribeLogcatDock(() => tick((n) => n + 1)), []);
  return getLogcatDock();
}

function useAndroid() {
  const [, tick] = useState(0);
  useEffect(() => subscribeAndroid(() => tick((n) => n + 1)), []);
  return getAndroid();
}

function useThreadId() {
  const [, tick] = useState(0);
  useEffect(() => subscribeSession(() => tick((n) => n + 1)), []);
  return getSession().threadId || "";
}

/** 底栏组件还在就收日志，包括收起、切到别的 Tab、关掉底栏（本组件仍挂着）。 */
function useLogcatStream(serial: string, connected: boolean) {
  useEffect(() => {
    if (connected && serial) ensureLogcat(serial);
    else stopLogcat();
  }, [connected, serial]);
  useEffect(() => () => stopLogcat(), []);
}

function TabBtn({
  id,
  active,
  label,
  fullscreen,
  onPick,
}: {
  id: DeviceDockTab;
  active: boolean;
  label: string;
  fullscreen: boolean;
  onPick: (tab: DeviceDockTab) => void;
}) {
  const { t } = useTranslation();
  const menu: MenuProps = {
    items: [
      {
        key: "fullscreen",
        label:
          fullscreen && active
            ? t("device.dockExitFullscreen")
            : t("device.dockFullscreen"),
        onClick: () => toggleDeviceDockFullscreen(id),
      },
      {
        key: "close",
        label: t("device.logcatClose"),
        onClick: () => setLogcatDockOpen(false),
      },
    ],
  };

  return (
    <Dropdown menu={menu} trigger={["contextMenu"]}>
      <button
        type="button"
        className="session-bottom-tab"
        data-active={active ? "1" : "0"}
        onClick={() => onPick(id)}
      >
        {label}
      </button>
    </Dropdown>
  );
}

export function DevicePerfBottom() {
  const { t } = useTranslation();
  const dock = useDock();
  const android = useAndroid();
  const threadId = useThreadId();
  useLogcatStream(android.serial || "", android.ok);
  const wrapRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);
  const [dragH, setDragH] = useState<number | null>(null);

  if (!dock.open) return null;

  const titleH = getLogcatTitleHeight();
  const displayH = dock.collapsed ? titleH : (dragH ?? dock.height);
  const logcatActive = isLogcatDockActive();
  const perfettoActive = dock.open && !dock.collapsed && dock.tab === "perfetto";
  const profilerActive = dock.open && !dock.collapsed && dock.tab === "profiler";
  const renderdocActive = dock.open && !dock.collapsed && dock.tab === "renderdoc";

  function onResizePointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    e.preventDefault();
    const startH = dock.collapsed ? titleH : dock.height;
    dragRef.current = { startY: e.clientY, startH };
    setDragH(startH);
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function onResizePointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const next = drag.startH + (drag.startY - e.clientY);
    const body = wrapRef.current?.parentElement;
    const max = body ? Math.floor(body.clientHeight * 0.6) : 480;
    setDragH(Math.min(max, Math.max(titleH, next)));
  }

  function onResizePointerUp(e: ReactPointerEvent<HTMLDivElement>) {
    if (!dragRef.current) return;
    dragRef.current = null;
    const body = wrapRef.current?.parentElement;
    const bodyH = body?.clientHeight || 600;
    const finalH = dragH ?? dock.height;
    setDragH(null);
    snapLogcatDockHeight(finalH, bodyH);
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
  }

  return (
    <div
      ref={wrapRef}
      className="session-bottom-panel"
      style={dock.fullscreen ? undefined : { height: displayH }}
      data-collapsed={dock.collapsed ? "1" : "0"}
      data-fullscreen={dock.fullscreen ? "1" : "0"}
    >
      {!dock.fullscreen ? (
        <div
          className="session-bottom-resize"
          onPointerDown={onResizePointerDown}
          onPointerMove={onResizePointerMove}
          onPointerUp={onResizePointerUp}
          onPointerCancel={onResizePointerUp}
          role="separator"
          aria-orientation="horizontal"
          aria-label={t("device.dockResize")}
        />
      ) : null}
      <div className="session-bottom-title">
        <div className="session-bottom-tabs" role="tablist">
          <TabBtn
            id="logcat"
            active={dock.tab === "logcat"}
            label={t("device.tabLogcat")}
            fullscreen={dock.fullscreen}
            onPick={setDeviceDockTab}
          />
          <TabBtn
            id="perfetto"
            active={dock.tab === "perfetto"}
            label={t("device.tabPerfetto")}
            fullscreen={dock.fullscreen}
            onPick={setDeviceDockTab}
          />
          <TabBtn
            id="profiler"
            active={dock.tab === "profiler"}
            label={t("device.tabProfiler")}
            fullscreen={dock.fullscreen}
            onPick={setDeviceDockTab}
          />
          <TabBtn id="renderdoc" active={dock.tab === "renderdoc"} label={t("renderdoc.tab")}
            fullscreen={dock.fullscreen} onPick={setDeviceDockTab} />
        </div>
        <div className="session-bottom-title-actions">
          {dock.fullscreen ? (
            <Button
              type="text"
              size="small"
              onClick={() => setDeviceDockFullscreen(false)}
            >
              {t("device.dockExitFullscreen")}
            </Button>
          ) : (
            <Button
              type="text"
              size="small"
              onClick={() => setLogcatDockCollapsed(!dock.collapsed)}
            >
              {dock.collapsed ? t("device.logcatExpand") : t("device.logcatCollapse")}
            </Button>
          )}
          <Button type="text" size="small" onClick={() => setLogcatDockOpen(false)}>
            {t("device.logcatClose")}
          </Button>
        </div>
      </div>
      {!dock.collapsed ? (
        <div className="session-bottom-body">
          {dock.tab === "logcat" ? (
            <LogcatPanel
              serial={android.serial || ""}
              connected={android.ok}
              active={logcatActive}
              foreground={android.foreground}
            />
          ) : dock.tab === "perfetto" ? (
            <PerfettoPanel
              serial={android.serial || ""}
              connected={android.ok}
              active={perfettoActive}
              threadId={threadId}
            />
          ) : dock.tab === "renderdoc" ? (
            <RenderDocPanel active={renderdocActive} />
          ) : (
            <ProfilerPanel
              serial={android.serial || ""}
              connected={android.ok}
              active={profilerActive}
              threadId={threadId}
            />
          )}
        </div>
      ) : null}
    </div>
  );
}
