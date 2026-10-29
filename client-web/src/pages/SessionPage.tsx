/**
 * 对话页：开场后输入；intent 分流；act 前批准。
 */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { App, Button, Input, Layout, List, Space, Tag, Typography } from "antd";
import { FeedMarkdown } from "../components/FeedMarkdown";
import { RoleBottom, RoleHeaderExtra, RoleSide, RoleStatus } from "../roles/chrome";
import { roleDef } from "../roles/registry";

import * as chatService from "../services/chatService";
import * as healthService from "../services/healthService";
import {
  applyStreamEvent,
  applyThreadGate,
  clearApproval,
  clearClarify,
  consumePendingAsks,
  enqueuePendingAsk,
  getSession,
  persistCurrentSessionMeta,
  setHealth,
  setRunning,
  subscribe,
} from "../store/sessionStore";
import { formatTokenCount } from "../utils/formatTokens";

const { Header, Sider, Content, Footer } = Layout;

type ResumeAction = "say" | "ask" | "approve" | "skip" | "reject";

type Props = { onExit?: () => void; onOpenSettings: () => void };

export function SessionPage({ onExit, onOpenSettings }: Props) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [, tick] = useState(0);
  useEffect(() => subscribe(() => tick((x) => x + 1)), []);
  const s = getSession();
  const mcpOk = !!s.health?.mcp_ok;
  const feedScrollRef = useRef<HTMLDivElement>(null);
  const [askText, setAskText] = useState("");
  const flushingRef = useRef(false);

  useEffect(() => {
    persistCurrentSessionMeta();
    void healthService
      .fetchHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  // 对话页也轮询，直到 RAG 预热结束（刚开服立刻进对话时）
  useEffect(() => {
    const st = s.health?.rag_status;
    if (st === "ready" || st === "error") return;
    const id = window.setInterval(() => {
      void healthService
        .fetchHealth()
        .then(setHealth)
        .catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(id);
  }, [s.health?.rag_status]);

  useEffect(() => {
    const cur = getSession();
    const tid = cur.threadId;
    if (!tid || !cur.started || cur.running) return;
    let cancelled = false;
    void chatService
      .fetchThreadGate(tid)
      .then((g) => {
        if (cancelled || getSession().running) return;
        applyThreadGate(g);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [s.threadId]);

  useEffect(() => {
    if (!s.started) return;
    const el = feedScrollRef.current;
    if (!el) return;
    const pin = () => {
      el.scrollTop = el.scrollHeight;
    };
    pin();
    requestAnimationFrame(pin);
  }, [s.started, s.feed.length, tick]);

  async function onStart() {
    if (!s.threadId) {
      message.error(t("session.noThread"));
      return;
    }
    if (roleDef(s.agentRole).requiresMcp && !mcpOk) {
      message.warning(t("session.mcpDown"));
      return;
    }
    setRunning(true);
    persistCurrentSessionMeta();
    applyStreamEvent({ type: "run_start", role: s.agentRole, thread_id: s.threadId });
    try {
      await chatService.streamChat({
        content: "",
        threadId: s.threadId,
        agentRole: s.agentRole,
        sessionPrefs: s.prefs,
        onEvent: applyStreamEvent,
      });
      if (getSession().running) {
        applyStreamEvent({ type: "error", content: "流已结束但未收到闸门/结束" });
      }
      persistCurrentSessionMeta();
    } catch (e) {
      message.error(String(e));
      applyStreamEvent({ type: "error", content: String(e) });
    }
  }

  async function onResume(action: ResumeAction, content?: string) {
    if (getSession().running) {
      message.warning(t("session.busy"));
      return;
    }
    if ((action === "say" || action === "ask") && content && !flushingRef.current) {
      applyStreamEvent({ type: "user", content });
    }
    if (action === "say" || action === "ask") {
      clearClarify();
    } else {
      clearApproval();
    }
    setRunning(true);
    try {
      await chatService.streamResume({
        threadId: s.threadId,
        action,
        content,
        onEvent: applyStreamEvent,
      });
      if (action === "say" || action === "ask") setAskText("");
    } catch (e) {
      message.error(String(e));
      setRunning(false);
    } finally {
      flushingRef.current = false;
    }
  }

  useEffect(() => {
    if (s.running || flushingRef.current) return;
    if (!s.needClarify && !s.needApproval) return;
    if (!s.pendingAsks.length) return;
    const text = consumePendingAsks();
    if (!text) return;
    flushingRef.current = true;
    void onResume("say", text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.needClarify, s.needApproval, s.running, s.pendingAsks.length]);

  function onAskSubmit() {
    const text = askText.trim();
    if (!text) {
      message.warning(t("session.askEmpty"));
      return;
    }
    const cur = getSession();
    if (cur.running) {
      enqueuePendingAsk(text);
      setAskText("");
      message.info(t("session.askQueue"));
      return;
    }
    if (cur.needClarify || cur.needApproval) {
      void onResume("say", text);
      return;
    }
    message.warning(t("session.busy"));
  }

  function onAskKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    e.stopPropagation();
    if (e.nativeEvent.isComposing) return;
    onAskSubmit();
  }

  const showAskBar =
    s.started && (s.needClarify || s.needApproval || s.running);

  const askHint = s.needApproval
    ? t("session.approvalAskHint")
    : s.running
      ? t("session.askQueue")
      : t("session.hitlHint");

  return (
    <Layout className="session-page">
      <Header className="session-header">
        <Typography.Text style={{ color: "#fff", fontSize: 12 }}>
          {t("common.appName")} · {s.displayName || s.agentRole}
        </Typography.Text>
        <Typography.Text style={{ color: "#aaa", fontSize: 11 }}>{s.threadId}</Typography.Text>
        <RoleStatus roleId={s.agentRole} health={s.health} />
        <RoleHeaderExtra roleId={s.agentRole} />
        {s.pendingAsks.length > 0 && (
          <Tag color="gold">
            {t("session.askQueuedMeta")} ×{s.pendingAsks.length}
          </Tag>
        )}
        {(s.promptTokens > 0 || s.completionTokens > 0 || s.started) && (
          <Typography.Text
            style={{ color: "#aaa", fontSize: 11 }}
            title={t("session.tokensExact", {
              prompt: s.promptTokens,
              completion: s.completionTokens,
            })}
          >
            {t("session.tokens", {
              prompt: formatTokenCount(s.promptTokens),
              completion: formatTokenCount(s.completionTokens),
            })}
          </Typography.Text>
        )}
        <Button type="link" style={{ color: "#fff" }} onClick={onOpenSettings}>{t("modelSettings.entry")}</Button>
        {onExit ? (
          <Button
            type="link"
            style={{ color: "#fff", marginLeft: "auto", fontSize: 12 }}
            onClick={onExit}
          >
            {t("session.exitToHome")}
          </Button>
        ) : null}
      </Header>
      <div className="session-mid">
        <Layout hasSider className="session-body">
          <Sider width={roleDef(s.agentRole).siderWidth} className="session-sider">
            <RoleSide roleId={s.agentRole} />
          </Sider>
          <Content className="session-main">
            {!s.started ? (
              <div style={{ textAlign: "center", padding: "48px 16px" }}>
                <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                  {t("session.emptyHint")}
                </Typography.Paragraph>
                <Button type="primary" size="large" onClick={() => void onStart()}>
                  {t("common.start")}
                </Button>
              </div>
            ) : (
              <div ref={feedScrollRef} className="session-feed">
                <List
                  size="small"
                  dataSource={s.feed}
                  renderItem={(item) => {
                    if (item.kind === "token") {
                      return (
                        <List.Item className="session-feed-item session-feed-item-token">
                          <FeedMarkdown text={item.text} />
                        </List.Item>
                      );
                    }
                    if (item.kind === "user") {
                      return (
                        <List.Item className="session-feed-item">
                          <span className="session-feed-meta">
                            <Tag>{t("session.feedUser")}</Tag> {item.text}
                          </span>
                        </List.Item>
                      );
                    }
                    if (item.kind === "phase") {
                      return (
                        <List.Item className="session-feed-item">
                          <span className="session-feed-phase">── {item.text} ──</span>
                        </List.Item>
                      );
                    }
                    if (item.kind === "tools") {
                      return (
                        <List.Item className="session-feed-item">
                          <span className="session-feed-meta">
                            <Tag>{t("session.feedTool")}</Tag> {item.text}
                          </span>
                        </List.Item>
                      );
                    }
                    if (item.kind === "error") {
                      return (
                        <List.Item className="session-feed-item">
                          <span className="session-feed-meta session-feed-error">
                            <Tag color="error">{t("session.feedError")}</Tag> {item.text}
                          </span>
                        </List.Item>
                      );
                    }
                    if (item.kind === "hitl") {
                      return (
                        <List.Item className="session-feed-item">
                          <span className="session-feed-meta">
                            <Tag color="orange">{t("session.feedHitl")}</Tag> {item.text}
                          </span>
                        </List.Item>
                      );
                    }
                    return (
                      <List.Item className="session-feed-item">
                        <span className="session-feed-meta">{item.text}</span>
                      </List.Item>
                    );
                  }}
                />
              </div>
            )}
          </Content>
        </Layout>
        <RoleBottom roleId={s.agentRole} />
      </div>
      {showAskBar && (
        <Footer className="session-footer">
          <div className="session-ask-bar">
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {askHint}
            </Typography.Text>
            <div style={{ display: "flex", gap: 8 }}>
              <Input
                value={askText}
                onChange={(e) => setAskText(e.target.value)}
                onKeyDown={onAskKeyDown}
                placeholder={t("session.askPlaceholder")}
                allowClear
              />
              <Button
                htmlType="button"
                type="primary"
                loading={s.running && !s.pendingAsks.length}
                onClick={() => onAskSubmit()}
              >
                {s.running ? t("session.askQueuedMeta") : t("session.askSend")}
              </Button>
            </div>
            {s.needApproval && (
              <Space wrap>
                <Typography.Text style={{ fontSize: 12 }}>
                  {s.planSummary || t("session.approveDefault")}
                </Typography.Text>
                <Button
                  htmlType="button"
                  type="primary"
                  disabled={s.running || s.pendingAsks.length > 0}
                  onClick={() => void onResume("approve")}
                >
                  {t("session.approve")}
                </Button>
                <Button
                  htmlType="button"
                  disabled={s.running || s.pendingAsks.length > 0}
                  onClick={() => void onResume("skip")}
                >
                  {t("session.skip")}
                </Button>
              </Space>
            )}
          </div>
        </Footer>
      )}
    </Layout>
  );
}
