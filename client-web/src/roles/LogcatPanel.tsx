/**
 * 真机 Logcat 视图。缓冲在 logcatStore，这里只画可见行。
 * 往上翻只停跟随，新日志仍进缓冲。
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Input, Select, Space, Typography } from "antd";
import { androidErrorText } from "../services/androidService";
import {
  clearLogcatDevice,
  getLogcatStatus,
  getLogcatView,
  restartLogcat,
  setLogcatQuery,
  stopLogcat,
  subscribeLogcat,
  takeLogcatTrim,
  type LogLine,
} from "../store/logcatStore";

const PRIORITY_OPTS = [
  { value: 2, labelKey: "device.logcatLevelV" },
  { value: 3, labelKey: "device.logcatLevelD" },
  { value: 4, labelKey: "device.logcatLevelI" },
  { value: 5, labelKey: "device.logcatLevelW" },
  { value: 6, labelKey: "device.logcatLevelE" },
] as const;

const ROW_H = 20;
const OVERSCAN = 8;
const LEVEL = ["?", "?", "V", "D", "I", "W", "E", "F"];

type Props = {
  serial: string;
  connected: boolean;
  /** 底栏 Logcat 页可见。只影响是否自动滚到底，不影响推流。 */
  active: boolean;
  foreground?: string;
};

function useLogcatSnap() {
  const [, tick] = useState(0);
  useEffect(() => subscribeLogcat(() => tick((n) => n + 1)), []);
  return { view: getLogcatView(), status: getLogcatStatus() };
}

export function LogcatPanel({ serial, connected, active, foreground = "" }: Props) {
  const { t } = useTranslation();
  const { view, status } = useLogcatSnap();
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const followRef = useRef(true);
  const [follow, setFollow] = useState(true);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportH, setViewportH] = useState(160);
  const [priority, setPriority] = useState(2);
  const [tag, setTag] = useState("");
  const [pkg, setPkg] = useState("");
  const [search, setSearch] = useState("");
  const [searchDebounced, setSearchDebounced] = useState("");
  const [clearErr, setClearErr] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => setSearchDebounced(search), 160);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    setLogcatQuery({
      priority,
      tag,
      package: pkg,
      text: searchDebounced,
    });
  }, [priority, tag, pkg, searchDebounced]);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setViewportH(el.clientHeight));
    ro.observe(el);
    setViewportH(el.clientHeight);
    return () => ro.disconnect();
  }, [connected]);

  useLayoutEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const trimmed = takeLogcatTrim();
    if (trimmed > 0 && !followRef.current) {
      el.scrollTop = Math.max(0, el.scrollTop - trimmed * ROW_H);
    }
    if (followRef.current && active) {
      el.scrollTop = el.scrollHeight;
    }
  }, [view.length, active, priority, tag, pkg, searchDebounced]);

  function onScroll() {
    const el = scrollerRef.current;
    if (!el) return;
    setScrollTop(el.scrollTop);
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (gap > 48) {
      if (followRef.current) {
        followRef.current = false;
        setFollow(false);
      }
    } else if (gap <= 8 && !followRef.current) {
      followRef.current = true;
      setFollow(true);
    }
  }

  async function onClear() {
    setClearErr("");
    const err = await clearLogcatDevice(serial);
    if (err) setClearErr(androidErrorText(new Error(err), t, "device.logcatClearFail"));
  }

  if (!connected) {
    return (
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: "8px 0 0" }}>
        {t("device.logcatNeedConnect")}
      </Typography.Paragraph>
    );
  }

  const start = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const visible = Math.ceil(viewportH / ROW_H) + OVERSCAN * 2;
  const slice = view.slice(start, start + visible);
  const error = status.error
    ? androidErrorText(new Error(status.error), t, "device.logcatStreamError")
    : clearErr;

  return (
    <div className="logcat-panel">
      <Space size={6} wrap className="logcat-toolbar">
        {status.running ? (
          <Button size="small" danger onClick={() => stopLogcat()}>
            {t("device.logcatStop")}
          </Button>
        ) : (
          <Button size="small" type="primary" onClick={() => restartLogcat(serial)}>
            {t("device.logcatStart")}
          </Button>
        )}
        <Button size="small" onClick={() => void onClear()}>
          {t("device.logcatClear")}
        </Button>
        <Button
          size="small"
          type={follow ? "primary" : "default"}
          ghost={follow}
          onClick={() => {
            const next = !follow;
            followRef.current = next;
            setFollow(next);
            const el = scrollerRef.current;
            if (next && el) el.scrollTop = el.scrollHeight;
          }}
        >
          {follow ? t("device.logcatFollowOn") : t("device.logcatFollowOff")}
        </Button>
        <Select
          size="small"
          style={{ width: 88 }}
          value={priority}
          options={PRIORITY_OPTS.map((o) => ({ value: o.value, label: t(o.labelKey) }))}
          onChange={setPriority}
        />
        <Input
          size="small"
          style={{ width: 140 }}
          placeholder={t("device.logcatSearchPh")}
          value={search}
          allowClear
          onChange={(e) => setSearch(e.target.value)}
        />
        <Input
          size="small"
          style={{ width: 90 }}
          placeholder={t("device.logcatTagPh")}
          value={tag}
          allowClear
          onChange={(e) => setTag(e.target.value)}
        />
        <Input
          size="small"
          style={{ width: 120 }}
          placeholder={t("device.logcatPkgPh")}
          value={pkg}
          allowClear
          onChange={(e) => setPkg(e.target.value)}
        />
        {foreground ? (
          <Button size="small" type="link" onClick={() => setPkg(foreground)}>
            {t("device.logcatUseFg")}
          </Button>
        ) : null}
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          {view.length}
        </Typography.Text>
      </Space>
      {error ? (
        <Typography.Text type="danger" style={{ fontSize: 11, display: "block" }}>
          {error}
        </Typography.Text>
      ) : null}
      <div className="logcat-view" ref={scrollerRef} onScroll={onScroll}>
        <div className="logcat-spacer" style={{ height: view.length * ROW_H }}>
          {slice.map((line, index) => (
            <LogRow key={line.id} line={line} top={(start + index) * ROW_H} />
          ))}
        </div>
      </div>
    </div>
  );
}

function LogRow({ line, top }: { line: LogLine; top: number }) {
  const level = LEVEL[line.priority] || "?";
  return (
    <div
      className={`logcat-row logcat-p-${level}`}
      style={{ transform: `translateY(${top}px)` }}
      title={line.message}
    >
      <span className="logcat-date">{line.date}</span>
      <span className="logcat-level">{level}</span>
      <span className="logcat-tag">{line.tag}</span>
      <span className="logcat-msg">{line.message}</span>
    </div>
  );
}
