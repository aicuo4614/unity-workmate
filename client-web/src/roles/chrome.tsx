/**
 * 顶栏状态 / 侧栏 / 底栏由扮演提供。对话页只查表，不按角色名写分支。
 */
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Tag } from "antd";
import type { Health } from "../services/types";
import { getAndroid, subscribeAndroid } from "../store/androidStore";
import {
  getLogcatDock,
  openDeviceDockTab,
  subscribeLogcatDock,
  type DeviceDockTab,
} from "../store/logcatDockStore";
import { DevicePerfBottom } from "./DevicePerfBottom";
import { DevicePerfSide } from "./DevicePerfSide";
import { ScenePerfSide } from "./ScenePerfSide";

const SIDES: Record<string, () => ReactNode> = {
  scene_perf: () => <ScenePerfSide />,
  device_perf: () => <DevicePerfSide />,
};

export function RoleSide({ roleId }: { roleId: string }) {
  const Side = SIDES[roleId] || SIDES.scene_perf;
  return <>{Side()}</>;
}

const BOTTOMS: Record<string, () => ReactNode> = {
  device_perf: () => <DevicePerfBottom />,
};

export function RoleBottom({ roleId }: { roleId: string }) {
  const Bottom = BOTTOMS[roleId];
  if (!Bottom) return null;
  return <>{Bottom()}</>;
}

function SceneStatus({ health }: { health: Health | null }) {
  const { t } = useTranslation();
  return (
    <>
      {health?.mcp_ok ? (
        <Tag color="green">MCP</Tag>
      ) : (
        <Tag color="red">MCP</Tag>
      )}
      {health?.rag_ready ? (
        <Tag color="green" title={health.rag_detail || undefined}>
          RAG
        </Tag>
      ) : health?.rag_status === "error" ? (
        <Tag color="red" title={health.rag_detail || undefined}>
          RAG
        </Tag>
      ) : (
        <Tag color="gold" title={health?.rag_detail || t("connect.ragLoading")}>
          RAG…
        </Tag>
      )}
      <Tag color="blue">{t("session.consultBadge")}</Tag>
    </>
  );
}

function DeviceStatus() {
  const { t } = useTranslation();
  const [, tick] = useState(0);
  useEffect(() => subscribeAndroid(() => tick((n) => n + 1)), []);
  const panel = getAndroid();
  if (panel.ok) {
    const name = panel.devices[0]?.model || panel.serial || "1";
    return <Tag color="green">{t("device.adbBadgeOn", { name })}</Tag>;
  }
  if (panel.error) {
    return <Tag color="red">{t("device.adbBadgeDown")}</Tag>;
  }
  return <Tag>{t("device.adbBadgeOff")}</Tag>;
}

const STATUSES: Record<string, (health: Health | null) => ReactNode> = {
  scene_perf: (health) => <SceneStatus health={health} />,
  device_perf: () => <DeviceStatus />,
};

export function RoleStatus({
  roleId,
  health,
}: {
  roleId: string;
  health: Health | null;
}) {
  const render = STATUSES[roleId] || STATUSES.scene_perf;
  return <>{render(health)}</>;
}

/** 顶栏扮演附加操作：Logcat / Perfetto 底栏入口并排 */
const HEADER_EXTRAS: Record<string, () => ReactNode> = {
  device_perf: () => <DeviceDockHeaderToggles />,
};

function DeviceDockHeaderToggles() {
  const { t } = useTranslation();
  const [, tick] = useState(0);
  useEffect(() => subscribeLogcatDock(() => tick((n) => n + 1)), []);
  const dock = getLogcatDock();

  function btn(tab: DeviceDockTab, label: string) {
    const on = dock.open && !dock.collapsed && dock.tab === tab;
    return (
      <Button
        key={tab}
        className="session-header-dock-btn"
        size="small"
        type={on ? "primary" : "default"}
        ghost
        onClick={() => openDeviceDockTab(tab)}
      >
        {label}
      </Button>
    );
  }

  return (
    <span className="session-header-dock-btns">
      {btn("logcat", t("device.tabLogcat"))}
      {btn("perfetto", t("device.tabPerfetto"))}
      {btn("profiler", t("device.tabProfiler"))}
      {btn("renderdoc", t("renderdoc.tab"))}
    </span>
  );
}

export function RoleHeaderExtra({ roleId }: { roleId: string }) {
  const Extra = HEADER_EXTRAS[roleId];
  if (!Extra) return null;
  return <>{Extra()}</>;
}
