import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, App, Button, Card, Form, Input, Space, Tag, Typography } from "antd";
import * as healthService from "../services/healthService";
import { defaultMcpForRole } from "../services/sessionRegistry";
import {
  setHealth,
  getSession,
  roleLabelKey,
  subscribe,
} from "../store/sessionStore";

type Props = { onNext: () => void; onBack: () => void };

/** 连接闸门：在选定助手/会话之后；URL 默认按扮演预填。 */
export function ConnectPage({ onNext, onBack }: Props) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [, tick] = useState(0);
  useEffect(() => subscribe(() => tick((x) => x + 1)), []);

  const session = getSession();
  const [url, setUrl] = useState(() => defaultMcpForRole(session.agentRole));
  const [busy, setBusy] = useState(false);

  const health = session.health;
  const mcpOk = !!health?.mcp_ok;
  const ragStatus = health?.rag_status || "pending";
  const ragReady = !!health?.rag_ready;
  const ragLoading = ragStatus === "loading" || ragStatus === "pending";

  async function refresh() {
    setBusy(true);
    try {
      const h = await healthService.fetchHealth();
      setHealth(h);
      if (h.mcp_url) setUrl(h.mcp_url);
    } catch (e) {
      setHealth(null);
      message.error(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function reconnect() {
    setBusy(true);
    try {
      const h = await healthService.reconnectMcp(url, true);
      setHealth(h);
      if (h.mcp_ok) {
        message.success(t("connect.mcpOk"));
      } else message.warning(h.mcp_error || t("connect.mcpFail"));
    } catch (e) {
      message.error(String(e));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    setUrl(defaultMcpForRole(session.agentRole));
    void refresh();
  }, [session.agentRole]);

  // 权重加载中：连接页轮询 /health，直到 ready/error
  useEffect(() => {
    if (!health?.ok) return;
    if (ragStatus === "ready" || ragStatus === "error") return;
    const id = window.setInterval(() => {
      void healthService
        .fetchHealth()
        .then(setHealth)
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(id);
  }, [health?.ok, ragStatus]);

  return (
    <Space
      orientation="vertical"
      size="large"
      style={{ width: "100%", maxWidth: 640, margin: "0 auto", display: "flex" }}
    >
      <Typography.Title level={3}>{t("connect.title")}</Typography.Title>
      <Typography.Paragraph type="secondary">
        <Tag>{t(roleLabelKey(session.agentRole))}</Tag>
        {session.displayName}
      </Typography.Paragraph>

      <Card>
        <Space orientation="vertical" style={{ width: "100%" }}>
          <div>
            {health?.ok ? (
              <Tag color="green">{t("connect.serverOk")}</Tag>
            ) : (
              <Tag color="red">{t("connect.serverFail")}</Tag>
            )}
          </div>
          <div>
            {mcpOk ? (
              <Tag color="green">{t("connect.mcpOk")}</Tag>
            ) : (
              <Tag color="red">{t("connect.mcpFail")}</Tag>
            )}
            {health && (
              <span style={{ marginLeft: 8 }}>
                {t("connect.tools")}: {health.mcp_tool_count} · {health.mcp_url}
              </span>
            )}
          </div>
          <div>
            {ragReady ? (
              <Tag color="green">{t("connect.ragReady")}</Tag>
            ) : ragStatus === "error" ? (
              <Tag color="red">{t("connect.ragError")}</Tag>
            ) : (
              <Tag color="gold">{t("connect.ragLoading")}</Tag>
            )}
          </div>
          {ragLoading && (
            <Alert
              type="info"
              showIcon
              message={t("connect.ragLoading")}
              description={
                <>
                  <div>{health?.rag_detail || t("connect.ragPending")}</div>
                  <div style={{ marginTop: 4 }}>{t("connect.ragWaitHint")}</div>
                </>
              }
            />
          )}
          {ragStatus === "error" && health?.rag_detail ? (
            <Alert type="warning" showIcon message={health.rag_detail} />
          ) : null}
          {ragReady && health?.rag_detail ? (
            <Typography.Text type="secondary">{health.rag_detail}</Typography.Text>
          ) : null}
          {health?.mcp_error ? (
            <Typography.Text type="danger">{health.mcp_error}</Typography.Text>
          ) : null}
          <Typography.Paragraph type="secondary">{t("connect.hint")}</Typography.Paragraph>
        </Space>
      </Card>

      {mcpOk && (
        <Card title={t("connect.confirmCard")}>
          <Typography.Text code>{health?.mcp_url}</Typography.Text>
        </Card>
      )}

      <Card title={t("connect.changeUrl")}>
        <Form layout="vertical" onFinish={() => void reconnect()}>
          <Form.Item label="URL">
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder={t("connect.urlPlaceholder")}
            />
          </Form.Item>
          <Space>
            <Button type="primary" htmlType="submit" loading={busy}>
              {t("connect.reconnect")}
            </Button>
            <Button onClick={() => void refresh()} loading={busy}>
              {t("common.retry")}
            </Button>
          </Space>
        </Form>
      </Card>

      <Space>
        <Button onClick={onBack}>{t("common.back")}</Button>
        <Button
          type="primary"
          disabled={!mcpOk}
          onClick={onNext}
        >
          {mcpOk ? t("common.next") : t("connect.cannotNext")}
        </Button>
      </Space>
    </Space>
  );
}
