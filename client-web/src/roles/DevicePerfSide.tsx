/**
 * 真机侧栏：ADB 连接 + 本局采样。Logcat 在底栏，不进侧栏。
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Select, Space, Typography } from "antd";
import { ANDROID_SERVER_DOWN, ANDROID_TIMEOUT } from "../services/androidService";
import {
  connectAndroid,
  disconnectAndroid,
  getAndroid,
  getAndroidCandidates,
  getSelectedSerial,
  isAndroidScanning,
  scanAndroid,
  setSelectedSerial,
  subscribeAndroid,
} from "../store/androidStore";
import type { AndroidDevice } from "../services/types";

const STATE_LABEL: Record<string, string> = {
  device: "device.stateOnline",
  unauthorized: "device.stateUnauthorized",
  offline: "device.stateOffline",
};

function useAndroidView() {
  const [, tick] = useState(0);
  useEffect(() => subscribeAndroid(() => tick((n) => n + 1)), []);
  return {
    panel: getAndroid(),
    candidates: getAndroidCandidates(),
    selected: getSelectedSerial(),
    scanning: isAndroidScanning(),
  };
}

function deviceLabel(item: AndroidDevice, stateLabel: string) {
  const name = item.model || item.serial;
  return item.model ? `${name}（${item.serial}）· ${stateLabel}` : `${name} · ${stateLabel}`;
}

export function DevicePerfSide() {
  const { t } = useTranslation();
  const { panel, candidates, selected, scanning } = useAndroidView();
  const connected = panel.devices[0];
  const busy = panel.loading || scanning;
  const serverDown =
    panel.error === ANDROID_SERVER_DOWN || panel.error === ANDROID_TIMEOUT;
  const sideHint =
    panel.error === ANDROID_SERVER_DOWN
      ? t("device.serverDown")
      : panel.error === ANDROID_TIMEOUT
        ? t("device.requestTimeout")
        : panel.error || t("device.pickHint");
  const emptyListHint = scanning
    ? t("device.scanning")
    : panel.error === ANDROID_SERVER_DOWN
      ? t("device.serverDownShort")
      : panel.error === ANDROID_TIMEOUT
        ? t("device.requestTimeout")
        : t("device.adbNone");

  useEffect(() => {
    void scanAndroid();
  }, []);

  return (
    <div className="role-side-split">
      <section aria-labelledby="adb-title">
        <Typography.Text id="adb-title" type="secondary" style={{ fontSize: 11 }}>
          {t("device.adbTitle")}
        </Typography.Text>

        {panel.ok ? (
          <>
            <div style={{ marginTop: 10 }}>
              <Typography.Text style={{ fontSize: 13 }}>
                {connected?.model || connected?.serial || panel.serial}
              </Typography.Text>
              {connected?.model ? (
                <Typography.Paragraph type="secondary" style={{ fontSize: 11, margin: "2px 0 0" }}>
                  {connected.serial}
                </Typography.Paragraph>
              ) : null}
              <Typography.Paragraph type="secondary" style={{ fontSize: 11, margin: "2px 0 0" }}>
                {t("device.stateOnline")}
              </Typography.Paragraph>
              <Typography.Paragraph style={{ fontSize: 12, margin: "8px 0 0", wordBreak: "break-all" }}>
                {panel.foreground
                  ? t("device.foreground", { name: panel.foreground })
                  : t("device.noForeground")}
              </Typography.Paragraph>
            </div>
            <Space size={8} style={{ marginTop: 8 }}>
              <Button size="small" loading={busy} onClick={() => connectAndroid()}>
                {t("device.refresh")}
              </Button>
              <Button size="small" danger loading={busy} onClick={() => disconnectAndroid()}>
                {t("device.disconnect")}
              </Button>
            </Space>
          </>
        ) : (
          <>
            <Typography.Paragraph
              type={serverDown ? "danger" : "secondary"}
              style={{ fontSize: 12, margin: "8px 0" }}
            >
              {sideHint}
            </Typography.Paragraph>
            <Select
              size="small"
              style={{ width: "100%" }}
              placeholder={t("device.pickPlaceholder")}
              value={selected || undefined}
              loading={scanning}
              options={candidates.map((item) => {
                const stateKey = STATE_LABEL[item.state] || "device.stateOther";
                return {
                  value: item.serial,
                  label: deviceLabel(item, t(stateKey, { state: item.state })),
                  disabled: item.state !== "device",
                };
              })}
              onOpenChange={(open) => {
                if (open) void scanAndroid();
              }}
              onChange={(value) => setSelectedSerial(String(value))}
              notFoundContent={emptyListHint}
            />
            <Space size={8} style={{ marginTop: 8 }}>
              <Button size="small" loading={scanning} onClick={() => void scanAndroid()}>
                {t("device.scan")}
              </Button>
              <Button
                size="small"
                type="primary"
                loading={panel.loading}
                disabled={!selected || scanning}
                onClick={() => connectAndroid()}
              >
                {t("device.connect")}
              </Button>
            </Space>
          </>
        )}
      </section>
      <section aria-labelledby="sample-title">
        <Typography.Text id="sample-title" type="secondary" style={{ fontSize: 11 }}>
          {t("device.sampleTitle")}
        </Typography.Text>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: "8px 0 0" }}>
          {t("device.sampleEmpty")}
        </Typography.Paragraph>
      </section>
    </div>
  );
}
