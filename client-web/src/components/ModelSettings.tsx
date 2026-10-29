/** 全局模型设置抽屉；明文 Key 仅保留在当前表单中。 */
import { useEffect, useState } from "react";
import { Alert, App, Button, Collapse, Drawer, Form, Input, InputNumber, Space, Tag } from "antd";
import { useTranslation } from "react-i18next";
import { fetchModelConfig, saveModelConfig, testModelConfig, type ModelInput, type ModelStatus } from "../services/modelService";

type Props = { open: boolean; onClose: () => void; onChange: (status: ModelStatus) => void };

export function ModelSettings({ open, onClose, onChange }: Props) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<ModelInput>();
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState(false);
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void fetchModelConfig().then((value) => {
      if (cancelled) return;
      setStatus(value);
      form.setFieldsValue({ base_url: value.base_url, model: value.model, request_timeout: value.request_timeout });
      setLoaded(true);
    }).catch(() => { if (!cancelled) setLoadError(true); });
    return () => { cancelled = true; };
  }, [open, form]);

  async function submit(action: "save" | "test" | "clear") {
    let input: ModelInput;
    try { input = await form.validateFields(); } catch { return; }
    // 留空表示沿用已保存的 Key；清除必须显式发送空串。
    input = { ...input, api_key: action === "clear" ? "" : input.api_key?.trim() || undefined };
    setBusy(true);
    try {
      if (action === "test") {
        await testModelConfig(input);
        message.success(t("modelSettings.testOk"));
      } else {
        const value = await saveModelConfig(input);
        setStatus(value);
        onChange(value);
        form.setFieldValue("api_key", "");
        message.success(t(action === "clear" ? "modelSettings.cleared" : "modelSettings.saved"));
      }
    } catch {
      message.error(t(action === "test" ? "modelSettings.testFailed" : "modelSettings.saveFailed"));
    } finally { setBusy(false); }
  }

  return (
    <Drawer title={t("modelSettings.title")} open={open} onClose={onClose} closable={!busy} maskClosable={!busy} keyboard={!busy}>
      <Space orientation="vertical" size="large" style={{ width: "100%" }}>
        <Alert type="info" showIcon title={t("modelSettings.hint")} />
        {loadError && <Alert type="error" title={t("modelSettings.loadFailed")} />}
        <Form form={form} layout="vertical" disabled={busy || !loaded}>
          <Form.Item label={t("modelSettings.provider")}><Input value="DeepSeek" readOnly /></Form.Item>
          <Form.Item label={t("modelSettings.status")}>
            <Tag color={status?.configured ? "green" : "orange"}>{t(status?.configured ? "modelSettings.configured" : "modelSettings.missing")}</Tag>
          </Form.Item>
          <Form.Item name="api_key" label={t("modelSettings.key")} extra={t("modelSettings.keyHint")}>
            <Input.Password autoComplete="new-password" placeholder={t(status?.configured ? "modelSettings.keepKey" : "modelSettings.enterKey")} />
          </Form.Item>
          <Collapse items={[{ key: "advanced", label: t("modelSettings.advanced"), children: <>
            <Form.Item name="base_url" label={t("modelSettings.baseUrl")} rules={[{ required: true, message: t("modelSettings.required") }]}><Input /></Form.Item>
            <Form.Item name="model" label={t("modelSettings.model")} rules={[{ required: true, whitespace: true, message: t("modelSettings.required") }]}><Input maxLength={128} /></Form.Item>
            <Form.Item name="request_timeout" label={t("modelSettings.timeout")} rules={[{ required: true, message: t("modelSettings.required") }]}><InputNumber min={5} max={600} /></Form.Item>
          </> }]} />
        </Form>
        <Space wrap>
          <Button disabled={busy || !loaded} onClick={() => void submit("test")}>{t("modelSettings.test")}</Button>
          <Button type="primary" loading={busy} disabled={!loaded} onClick={() => void submit("save")}>{t("modelSettings.save")}</Button>
          <Button danger disabled={busy || !loaded || !status?.configured} onClick={() => void submit("clear")}>{t("modelSettings.clear")}</Button>
        </Space>
      </Space>
    </Drawer>
  );
}
