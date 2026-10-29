/**
 * 场景优化侧栏：图上的站。真机扮演不使用这块。
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Typography } from "antd";
import { getSession, subscribe } from "../store/sessionStore";

const FLOW_TRUNK = ["greet", "hitl", "intent"] as const;
const FLOW_BRANCHES = ["analyze", "act"] as const;

function resolveActiveNode(s: {
  phase: string | null;
  needClarify: boolean;
  needApproval: boolean;
  started: boolean;
  running: boolean;
}): string | null {
  if (!s.started) return null;
  if (s.needApproval) return "act";
  if (s.needClarify && !s.running) return "hitl";
  return s.phase;
}

function FlowNode({
  id,
  active,
  dim,
  label,
}: {
  id: string;
  active: boolean;
  dim?: boolean;
  label: string;
}) {
  return (
    <div
      style={{
        padding: "4px 8px",
        borderRadius: 4,
        border: active ? "1px solid #1677ff" : "1px solid #e8e8e8",
        background: active ? "#e6f4ff" : dim ? "#fafafa" : "#fff",
        color: active ? "#1677ff" : dim ? "#bbb" : "#333",
        fontWeight: active ? 600 : 400,
        fontSize: 11,
        lineHeight: 1.4,
      }}
      title={id}
    >
      {label}
    </div>
  );
}

function FlowArrow() {
  return (
    <div style={{ textAlign: "center", color: "#bbb", fontSize: 11, lineHeight: 1.2 }}>
      ↓
    </div>
  );
}

export function ScenePerfSide() {
  const { t } = useTranslation();
  const [, tick] = useState(0);
  useEffect(() => subscribe(() => tick((x) => x + 1)), []);
  const s = getSession();
  void tick;
  const activeNode = resolveActiveNode(s);

  return (
    <>
      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
        {t("session.phaseRail")}
      </Typography.Text>
      <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 2 }}>
        {FLOW_TRUNK.map((id, i) => (
          <div key={id}>
            {i > 0 ? <FlowArrow /> : null}
            <FlowNode
              id={id}
              active={activeNode === id}
              dim={!s.started}
              label={t(`phase.${id}`)}
            />
          </div>
        ))}
        <FlowArrow />
        <Typography.Text type="secondary" style={{ fontSize: 11, margin: "2px 0" }}>
          {t("session.flowBranch")}
        </Typography.Text>
        {FLOW_BRANCHES.map((id) => (
          <div key={id} style={{ marginBottom: 4 }}>
            <FlowNode
              id={id}
              active={activeNode === id}
              dim={!s.started || activeNode !== id}
              label={t(`phase.${id}`)}
            />
          </div>
        ))}
        <FlowArrow />
        <Typography.Text
          type="secondary"
          style={{
            fontSize: 11,
            color: activeNode === "hitl" && s.needClarify ? "#1677ff" : "#bbb",
          }}
        >
          {t("session.flowBack")}
        </Typography.Text>
      </div>
    </>
  );
}
