import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, App, Button, Card, Modal, Tag, Typography } from "antd";
import { SwipeAction } from "antd-mobile";
import {
  deleteSession,
  listSessions,
  type SavedSession,
} from "../services/sessionRegistry";
import { openSavedSession, roleLabelKey } from "../store/sessionStore";

import type { ModelStatus } from "../services/modelService";

type Props = {
  onOpenSettings: () => void;
  modelStatus: ModelStatus | null;
  onNewSession: () => void;
  onOpenRecent: () => void;
};

/** 入口工作台：最近会话支持左滑删除（antd-mobile SwipeAction 动效）。 */
export function HomePage({ onNewSession, onOpenRecent, onOpenSettings, modelStatus }: Props) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [listRev, setListRev] = useState(0);
  // listRev 变化才重读 localStorage（勿依赖 setState 函数本身，其引用永远不变）
  const recent = useMemo(() => listSessions(), [listRev]);

  function onOpen(rec: SavedSession) {
    openSavedSession(rec);
    onOpenRecent();
  }

  function onDelete(rec: SavedSession) {
    Modal.confirm({
      title: t("home.deleteConfirmTitle"),
      content: t("home.deleteConfirmBody", { name: rec.displayName }),
      okText: t("home.delete"),
      okType: "danger",
      cancelText: t("common.back"),
      onOk: () => {
        deleteSession(rec.threadId);
        setListRev((x) => x + 1);
        message.success(t("home.deleted"));
      },
    });
  }

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", width: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <Typography.Title level={3} style={{ marginBottom: 8 }}>
        {t("home.title")}
      </Typography.Title>
        <Button onClick={onOpenSettings}>{t("modelSettings.entry")}</Button>
      </div>
      <Alert style={{ marginBottom: 16 }} type={modelStatus?.configured ? "success" : "warning"} title={t(modelStatus?.configured ? "modelSettings.configured" : "modelSettings.setupHint")} action={<Button size="small" onClick={onOpenSettings}>{t("modelSettings.setup")}</Button>} />
      <Typography.Paragraph type="secondary" style={{ marginBottom: 24 }}>
        {t("home.landingHint")}
      </Typography.Paragraph>

      <Card
        hoverable
        onClick={onNewSession}
        style={{ cursor: "pointer", marginBottom: 32, maxWidth: 360 }}
      >
        <Typography.Text strong>{t("home.newSession")}</Typography.Text>
        <div style={{ marginTop: 8 }}>
          <Typography.Text type="secondary">{t("home.newSessionHint")}</Typography.Text>
        </div>
      </Card>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginBottom: 12,
        }}
      >
        <Typography.Text strong>{t("home.recent")}</Typography.Text>
        <Typography.Text type="secondary">
          {t("home.recentCount", { count: recent.length })}
        </Typography.Text>
      </div>

      <Card styles={{ body: { padding: 0, overflow: "hidden" } }}>
        {recent.length === 0 ? (
          <div style={{ padding: 24 }}>
            <Typography.Text type="secondary">{t("home.emptyRecent")}</Typography.Text>
          </div>
        ) : (
          recent.map((rec, i) => (
            <SwipeAction
              key={rec.threadId}
              closeOnAction
              rightActions={[
                {
                  key: "delete",
                  text: t("home.delete"),
                  color: "danger",
                },
              ]}
              onAction={(action) => {
                if (action.key === "delete") onDelete(rec);
              }}
            >
              <div
                onClick={() => onOpen(rec)}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 16,
                  padding: "14px 20px",
                  cursor: "pointer",
                  background: "#fff",
                  borderTop: i === 0 ? undefined : "1px solid #f0f0f0",
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <Typography.Text strong ellipsis>
                    {rec.displayName}
                  </Typography.Text>
                  <div>
                    <Tag style={{ marginInlineEnd: 8 }}>{t(roleLabelKey(rec.agentRole))}</Tag>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {rec.threadId}
                    </Typography.Text>
                  </div>
                </div>
                <Typography.Text type="secondary" style={{ flexShrink: 0, fontSize: 12 }}>
                  {new Date(rec.updatedAt).toLocaleString()}
                </Typography.Text>
              </div>
            </SwipeAction>
          ))
        )}
      </Card>
    </div>
  );
}
