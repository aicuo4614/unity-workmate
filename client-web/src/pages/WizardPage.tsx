import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { App, Button, Card, Form, Input, Radio, Select, Space, Typography } from "antd";
import type { SessionPrefs } from "../services/types";
import { ROLE_DEFS, roleDef } from "../roles/registry";
import { isNameTaken } from "../services/sessionRegistry";
import {
  getSession,
  prepareAssistantDraft,
  setDisplayName,
  setPrefs,
  subscribe,
} from "../store/sessionStore";

type Props = {
  step: "assistant" | "prefs";
  onNext: () => void;
  onBack: () => void;
};

export function WizardPage({ step, onNext, onBack }: Props) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [, tick] = useState(0);
  useEffect(() => subscribe(() => tick((x) => x + 1)), []);

  const session = getSession();
  const prefs = session.prefs;

  useEffect(() => {
    if (step !== "assistant") return;
    // 每次进选助手：按当前扮演生成不重名默认名（工作台「新建」入口）
    const role = session.agentRole || "scene_perf";
    const prefix = t(roleDef(role).namePrefixKey);
    prepareAssistantDraft(role, prefix);
  }, [step]);

  function update(partial: Partial<SessionPrefs>) {
    setPrefs({ ...prefs, ...partial });
  }

  function onRoleChange(role: string) {
    const prefix = t(roleDef(role).namePrefixKey);
    prepareAssistantDraft(role, prefix);
  }

  function onAssistantNext() {
    const name = session.displayName.trim();
    if (!name) {
      message.warning(t("home.nameRequired"));
      return;
    }
    if (isNameTaken(session.agentRole, name, session.threadId || undefined)) {
      message.error(t("home.nameTaken"));
      return;
    }
    onNext();
  }

  if (step === "assistant") {
    return (
      <Space
        orientation="vertical"
        size="large"
        style={{ width: "100%", maxWidth: 640, margin: "0 auto", display: "flex" }}
      >
        <Typography.Title level={3}>{t("wizard.assistant")}</Typography.Title>
        <Card>
          <Form layout="vertical">
            <Form.Item label={t("wizard.assistant")}>
              <Radio.Group
                value={session.agentRole}
                onChange={(e) => onRoleChange(e.target.value)}
              >
                {ROLE_DEFS.map((role) => (
                  <Radio key={role.id} value={role.id}>
                    {t(role.labelKey)}
                  </Radio>
                ))}
              </Radio.Group>
            </Form.Item>
            <Form.Item
              label={t("home.sessionName")}
              extra={t("home.sessionNameHint")}
              required
            >
              <Input
                value={session.displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder={t("home.sessionNamePlaceholder")}
                maxLength={64}
              />
            </Form.Item>
          </Form>
        </Card>
        <Space>
          <Button onClick={onBack}>{t("common.back")}</Button>
          <Button type="primary" onClick={onAssistantNext}>
            {t("common.next")}
          </Button>
        </Space>
      </Space>
    );
  }

  // scene_perf：主维 / 平台口吻。采数深度由对话里的模型挑工具，不再单设一档。
  return (
    <Space
      orientation="vertical"
      size="large"
      style={{ width: "100%", maxWidth: 640, margin: "0 auto", display: "flex" }}
    >
      <Typography.Title level={3}>{t("wizard.prefs")}</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        {t("wizard.prefsConsultHint")}
      </Typography.Paragraph>
      <Form layout="vertical">
        <Form.Item label={t("wizard.primaryMetric")}>
          <Select
            value={prefs.primary_metric}
            onChange={(v) => update({ primary_metric: v })}
            options={[
              { value: "batches", label: t("wizard.metric_batches") },
              { value: "cpu_frame", label: t("wizard.metric_cpu") },
              { value: "post_process", label: t("wizard.metric_pp") },
              { value: "overdraw", label: t("wizard.metric_od") },
              { value: "balanced", label: t("wizard.metric_balanced") },
            ]}
          />
        </Form.Item>
        <Form.Item label={t("wizard.platformTone")}>
          <Select
            value={prefs.platform_tone}
            onChange={(v) => update({ platform_tone: v })}
            options={[
              { value: "mobile_mid", label: t("wizard.tone_mid") },
              { value: "mobile_high", label: t("wizard.tone_high") },
              { value: "editor_pc", label: t("wizard.tone_pc") },
            ]}
          />
        </Form.Item>
      </Form>
      <Space>
        <Button onClick={onBack}>{t("common.back")}</Button>
        <Button type="primary" onClick={onNext}>
          {t("common.next")}
        </Button>
      </Space>
    </Space>
  );
}
