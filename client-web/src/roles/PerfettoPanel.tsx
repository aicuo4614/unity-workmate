/**
 * 真机 Perfetto 面板：顶栏采录 / 左历史 / 右诊断。
 * 历史落盘在 server/logs/perfetto，关会话后仍可打开。
 */
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { Button, Segmented, Typography } from "antd";
import {
  androidErrorText,
  androidPerfettoDownloadUrl,
  captureAndroidPerfetto,
  fetchAndroidPerfettoHistory,
  fetchAndroidPerfettoLatest,
  loadAndroidPerfettoHistory,
} from "../services/androidService";
import type {
  AndroidPerfettoHistoryItem,
  AndroidPerfettoResult,
  AndroidPerfettoSummary,
} from "../services/types";

type Props = {
  serial: string;
  connected: boolean;
  active: boolean;
  /** 会话 thread_id：trace 文件名前缀与历史过滤键 */
  threadId: string;
};

const SPLIT_KEY = "ailearning.perfetto.split.v1";
const SPLIT_MIN = 160;
const SPLIT_MAX = 480;
const SPLIT_DEFAULT = 260;

function readSplitWidth(): number {
  try {
    const n = Number(localStorage.getItem(SPLIT_KEY));
    if (Number.isFinite(n) && n >= SPLIT_MIN && n <= SPLIT_MAX) return n;
  } catch {
    /* ignore */
  }
  return SPLIT_DEFAULT;
}

function fmtMs(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v}`;
}

function fmtHistTime(iso: string): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="perfetto-metric" title={hint}>
      <span className="perfetto-metric-label">{label}</span>
      <strong className="perfetto-metric-value">{value}</strong>
      <span className="perfetto-metric-hint">{hint}</span>
    </div>
  );
}

function Section({
  title,
  hint,
  children,
  defaultOpen = true,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="perfetto-section" data-open={open ? "1" : "0"}>
      <button
        type="button"
        className="perfetto-section-head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="perfetto-section-title">{title}</span>
        {hint ? <span className="perfetto-section-hint">{hint}</span> : null}
        <span className="perfetto-section-chevron">{open ? "▾" : "▸"}</span>
      </button>
      {open ? <div className="perfetto-section-body">{children}</div> : null}
    </section>
  );
}

function FrameSparkline({ series }: { series: number[] }) {
  const points = useMemo(() => {
    if (!series.length) return "";
    const w = 320;
    const h = 56;
    const max = Math.max(...series, 16.67);
    const span = Math.max(0.001, max);
    return series
      .map((v, i) => {
        const x = series.length === 1 ? 0 : (i / (series.length - 1)) * w;
        const y = h - (v / span) * (h - 4) - 2;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }, [series]);

  if (!series.length) return null;
  const max = Math.max(...series, 16.67);
  const line16 = 56 - (16.67 / max) * (56 - 4) - 2;

  return (
    <svg className="perfetto-spark" viewBox="0 0 320 56" preserveAspectRatio="none" aria-hidden>
      <line x1="0" y1={line16} x2="320" y2={line16} className="perfetto-spark-ref" />
      <polyline fill="none" points={points} className="perfetto-spark-line" />
    </svg>
  );
}

function SummaryBody({
  summary,
  packageName,
}: {
  summary: AndroidPerfettoSummary;
  packageName: string;
}) {
  const { t } = useTranslation();
  const blame = summary.blame || { app: 0, sf: 0, none: 0, other: 0 };
  const breakdown = summary.jank_breakdown || [];
  const series = summary.frame_series_ms || [];
  const blameTotal = Math.max(1, blame.app + blame.sf + blame.none + (blame.other || 0));

  return (
    <div className="perfetto-summary">
      <div className="perfetto-result-meta">
        {packageName ? (
          <span>
            {t("device.perfettoPackage", { name: packageName })}
            {summary.scope === "package"
              ? ` · ${t("device.perfettoScopePkg")}`
              : ` · ${t("device.perfettoScopeAll")}`}
          </span>
        ) : null}
        {summary.notes?.length ? <span>{summary.notes.join(" · ")}</span> : null}
      </div>

      <Section title={t("device.perfettoSecOverview")}>
        <div className="perfetto-metrics">
          <Metric
            label={t("device.perfettoFps")}
            value={fmtMs(summary.fps_est)}
            hint={t("device.perfettoFpsHint")}
          />
          <Metric
            label={t("device.perfettoAvg")}
            value={fmtMs(summary.avg_frame_ms)}
            hint={t("device.perfettoAvgHint")}
          />
          <Metric
            label={t("device.perfettoP95")}
            value={fmtMs(summary.p95_frame_ms)}
            hint={t("device.perfettoP95Hint")}
          />
          <Metric
            label={t("device.perfettoMax")}
            value={fmtMs(summary.max_frame_ms)}
            hint={t("device.perfettoMaxHint")}
          />
          <Metric
            label={t("device.perfettoFrames")}
            value={String(summary.frame_count)}
            hint={t("device.perfettoFramesHint")}
          />
          <Metric
            label={t("device.perfettoJankTyped")}
            value={String(summary.jank_typed_count ?? summary.jank_count ?? "—")}
            hint={t("device.perfettoJankTypedHint")}
          />
        </div>
      </Section>

      {series.length ? (
        <Section title={t("device.perfettoSpark")} hint={t("device.perfettoSparkRef")}>
          <FrameSparkline series={series} />
        </Section>
      ) : null}

      <Section title={t("device.perfettoBlame")}>
        <div className="perfetto-blame-bars">
          {(
            [
              ["app", blame.app, t("device.perfettoBlameApp")],
              ["sf", blame.sf, t("device.perfettoBlameSf")],
              ["none", blame.none, t("device.perfettoBlameOk")],
              ["other", blame.other || 0, t("device.perfettoBlameOther")],
            ] as const
          )
            .filter(([key, n]) => key !== "other" || n > 0)
            .map(([key, n, label]) => (
              <div key={key} className="perfetto-blame-item">
                <div className="perfetto-blame-label">
                  <span>{label}</span>
                  <strong>{n}</strong>
                </div>
                <div className="perfetto-blame-track">
                  <i
                    className={`perfetto-blame-fill is-${key}`}
                    style={{ width: `${Math.round((n / blameTotal) * 100)}%` }}
                  />
                </div>
              </div>
            ))}
        </div>
      </Section>

      {breakdown.length ? (
        <Section title={t("device.perfettoJankTable")} defaultOpen={false}>
          <table className="perfetto-jank-table">
            <thead>
              <tr>
                <th>{t("device.perfettoJankType")}</th>
                <th>{t("device.perfettoJankBucket")}</th>
                <th>{t("device.perfettoJankCount")}</th>
                <th>%</th>
              </tr>
            </thead>
            <tbody>
              {breakdown.map((row) => (
                <tr key={row.jank_type}>
                  <td>{row.jank_type}</td>
                  <td>{t(`device.perfettoBucket_${row.bucket}`, { defaultValue: row.bucket })}</td>
                  <td>{row.count}</td>
                  <td>{row.pct}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      ) : null}

      <Section title={t("device.perfettoCpuEst")}>
        <div className="perfetto-kv-row">
          <span>
            {t("device.perfettoCpuPerFrame")}:{" "}
            <strong>{fmtMs(summary.cpu_pkg_per_frame_ms)}</strong> ms
          </span>
          <span>
            {t("device.perfettoCpuTotal")}:{" "}
            <strong>{fmtMs(summary.cpu_pkg_total_ms)}</strong> ms
          </span>
        </div>
        {summary.cpu_pkg_note ? (
          <p className="perfetto-note">{summary.cpu_pkg_note}</p>
        ) : null}
        {summary.cpu_top?.length ? (
          <details className="perfetto-details">
            <summary>{t("device.perfettoCpuTop")}</summary>
            <ul className="perfetto-cpu-list">
              {summary.cpu_top.slice(0, 8).map((row) => (
                <li key={row.name}>
                  <span className="perfetto-cpu-name">{row.name}</span>
                  <span>{row.cpu_ms} ms</span>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </Section>

      <Section title={t("device.perfettoGpu")} hint={t("device.perfettoGpuSectionHint")}>
        <div className="perfetto-kv-row">
          <span title={t("device.perfettoGpuFreqHint")}>
            {t("device.perfettoGpuFreq")}:{" "}
            <strong>{fmtMs(summary.gpu_freq_mhz_avg)}</strong> MHz
            {summary.gpu_freq_mhz_min != null && summary.gpu_freq_mhz_max != null
              ? ` (${summary.gpu_freq_mhz_min}–${summary.gpu_freq_mhz_max})`
              : ""}
          </span>
          <span title={t("device.perfettoGpuTempHint")}>
            {t("device.perfettoGpuTemp")}:{" "}
            <strong>{fmtMs(summary.gpu_temp_c_avg)}</strong> °C
          </span>
          <span title={t("device.perfettoGpuWaitHint")}>
            {t("device.perfettoGpuWait")}: <strong>{summary.gpu_wait_count ?? 0}</strong>
            {summary.gpu_wait_max_ms != null
              ? ` · ${t("device.perfettoGpuWaitMax")} ${summary.gpu_wait_max_ms} ms`
              : ""}
          </span>
          <span>
            {t("device.perfettoGpuCounters")}:{" "}
            <strong>
              {summary.gpu_counters_ok
                ? t("device.perfettoGpuCountersYes")
                : t("device.perfettoGpuCountersNo")}
            </strong>
          </span>
        </div>
        {summary.gpu_note ? <p className="perfetto-note">{summary.gpu_note}</p> : null}
      </Section>
    </div>
  );
}

function HistoryPane({
  history,
  histError,
  histLoading,
  selectedId,
  busy,
  loadingId,
  onRefresh,
  onOpen,
}: {
  history: AndroidPerfettoHistoryItem[];
  histError: string;
  histLoading: boolean;
  selectedId: string;
  busy: boolean;
  loadingId: string;
  onRefresh: () => void;
  onOpen: (id: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <aside className="perfetto-history">
      <div className="perfetto-history-head">
        <span className="perfetto-history-title">{t("device.perfettoHistory")}</span>
        <button
          type="button"
          className="perfetto-history-refresh"
          disabled={histLoading}
          onClick={onRefresh}
        >
          {histLoading ? t("device.perfettoHistLoading") : t("device.perfettoHistRefresh")}
        </button>
      </div>
      {histError ? (
        <p className="perfetto-history-msg is-error">{histError}</p>
      ) : history.length === 0 ? (
        <p className="perfetto-history-msg">
          {histLoading ? t("device.perfettoHistLoading") : t("device.perfettoHistEmpty")}
        </p>
      ) : (
        <ul className="perfetto-history-list">
          {history.map((item) => {
            const activeRow = item.id === selectedId;
            const meta = [
              item.package || item.serial || "",
              item.fps_est != null ? `${item.fps_est} FPS` : "",
              fmtSize(item.size_bytes),
            ]
              .filter(Boolean)
              .join(" · ");
            return (
              <li key={item.id} className={activeRow ? "is-active" : undefined}>
                <button
                  type="button"
                  className="perfetto-history-item"
                  disabled={!!loadingId || busy}
                  onClick={() => onOpen(item.id)}
                >
                  <span className="perfetto-history-time">{fmtHistTime(item.mtime)}</span>
                  <span className="perfetto-history-meta">
                    {meta || item.id}
                    {!item.has_summary ? ` · ${t("device.perfettoHistNoSum")}` : ""}
                  </span>
                </button>
                <a
                  className="perfetto-history-dl"
                  href={androidPerfettoDownloadUrl(item.id)}
                  target="_blank"
                  rel="noreferrer"
                  title={t("device.perfettoDownload")}
                >
                  ↓
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}

export function PerfettoPanel({ serial, connected, active, threadId }: Props) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [loadingId, setLoadingId] = useState("");
  const [durationSec, setDurationSec] = useState<5 | 10>(5);
  const [result, setResult] = useState<AndroidPerfettoResult | null>(null);
  const [history, setHistory] = useState<AndroidPerfettoHistoryItem[]>([]);
  const [histError, setHistError] = useState("");
  const [histLoading, setHistLoading] = useState(false);
  const [splitW, setSplitW] = useState(readSplitWidth);
  const bodyRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);
  const splitLiveRef = useRef(splitW);

  async function refreshHistory() {
    if (!threadId) {
      setHistory([]);
      setHistError("");
      return;
    }
    setHistLoading(true);
    try {
      const data = await fetchAndroidPerfettoHistory(40, threadId);
      if (data.ok) {
        setHistory(data.items || []);
        setHistError("");
      } else {
        setHistory([]);
        setHistError(data.error || t("device.perfettoHistFail"));
      }
    } catch (e) {
      setHistory([]);
      setHistError(androidErrorText(e, t, "device.perfettoHistFail"));
    } finally {
      setHistLoading(false);
    }
  }

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setResult(null);
    void fetchAndroidPerfettoLatest(threadId)
      .then((data) => {
        if (!cancelled && (data.status === "done" || data.summary)) setResult(data);
      })
      .catch(() => {
        /* 首次无内存态可忽略 */
      });
    void refreshHistory();
    return () => {
      cancelled = true;
    };
  }, [active, threadId]);

  async function onCapture() {
    if (!connected || !serial || !threadId || busy) return;
    setBusy(true);
    try {
      const data = await captureAndroidPerfetto({
        serial,
        duration_sec: durationSec,
        thread_id: threadId,
      });
      setResult(data);
      await refreshHistory();
    } catch (e) {
      setResult({
        ok: false,
        error: e instanceof Error ? e.message : t("device.perfettoFail"),
        status: "error",
        serial,
        package: "",
        duration_sec: durationSec,
        started_at: "",
        finished_at: "",
        trace_path: "",
        summary: null,
        thread_id: threadId,
      });
    } finally {
      setBusy(false);
    }
  }

  async function onOpenHistory(id: string) {
    if (!id || busy || loadingId) return;
    setLoadingId(id);
    try {
      const data = await loadAndroidPerfettoHistory(id, threadId);
      setResult(data);
    } catch (e) {
      setResult({
        ok: false,
        error: e instanceof Error ? e.message : t("device.perfettoFail"),
        status: "error",
        serial: "",
        package: "",
        duration_sec: 0,
        started_at: "",
        finished_at: "",
        trace_path: "",
        summary: null,
        id,
        thread_id: threadId,
      });
    } finally {
      setLoadingId("");
    }
  }

  function onSplitDown(e: ReactPointerEvent<HTMLDivElement>) {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startW: splitLiveRef.current };
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function onSplitMove(e: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const bodyW = bodyRef.current?.clientWidth || 800;
    const max = Math.min(SPLIT_MAX, Math.floor(bodyW * 0.55));
    const next = Math.min(max, Math.max(SPLIT_MIN, drag.startW + (e.clientX - drag.startX)));
    splitLiveRef.current = next;
    setSplitW(next);
  }

  function onSplitUp(e: ReactPointerEvent<HTMLDivElement>) {
    if (!dragRef.current) return;
    dragRef.current = null;
    try {
      localStorage.setItem(SPLIT_KEY, String(splitLiveRef.current));
    } catch {
      /* ignore */
    }
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
  }

  const canDownload = !!(result?.ok && (result.id || result.trace_path));
  const downloadName = result?.id || "";
  const selectedId = result?.id || "";
  const statusText = busy
    ? t("device.perfettoRunning")
    : loadingId
      ? t("device.perfettoLoadingHist")
      : !threadId
        ? t("device.perfettoNeedSession")
        : connected
          ? t("device.perfettoHint")
          : t("device.perfettoNeedConnect");

  return (
    <div className="perfetto-panel">
      <div className="perfetto-toolbar">
        <div className="perfetto-toolbar-main">
          <Segmented
            size="small"
            value={durationSec}
            disabled={busy}
            onChange={(v) => setDurationSec(v as 5 | 10)}
            options={[
              { label: t("device.perfettoDur5"), value: 5 },
              { label: t("device.perfettoDur10"), value: 10 },
            ]}
          />
          <Button
            size="small"
            type="primary"
            loading={busy}
            disabled={!connected || !serial || !threadId}
            onClick={() => void onCapture()}
          >
            {t("device.perfettoCapture")}
          </Button>
          <Button
            size="small"
            disabled={!canDownload}
            href={canDownload ? androidPerfettoDownloadUrl(downloadName || undefined) : undefined}
            target="_blank"
          >
            {t("device.perfettoDownload")}
          </Button>
          <a
            className="perfetto-ui-link"
            href="https://ui.perfetto.dev"
            target="_blank"
            rel="noreferrer"
          >
            {t("device.perfettoOpenUi")}
          </a>
        </div>
        <div className="perfetto-toolbar-meta">
          {connected && serial ? (
            <span className="perfetto-chip">
              {t("device.perfettoDevice", { serial })}
            </span>
          ) : (
            <span className="perfetto-chip is-warn">{t("device.perfettoOffline")}</span>
          )}
          {result?.package ? (
            <span className="perfetto-chip">{result.package}</span>
          ) : null}
          <Typography.Text type="secondary" className="perfetto-status">
            {statusText}
          </Typography.Text>
        </div>
      </div>

      <div
        ref={bodyRef}
        className="perfetto-body"
        style={{ gridTemplateColumns: `${splitW}px 6px minmax(0, 1fr)` }}
      >
        <HistoryPane
          history={history}
          histError={histError}
          histLoading={histLoading}
          selectedId={selectedId}
          busy={busy}
          loadingId={loadingId}
          onRefresh={() => void refreshHistory()}
          onOpen={(id) => void onOpenHistory(id)}
        />

        <div
          className="perfetto-split"
          onPointerDown={onSplitDown}
          onPointerMove={onSplitMove}
          onPointerUp={onSplitUp}
          onPointerCancel={onSplitUp}
          role="separator"
          aria-orientation="vertical"
          aria-label={t("device.perfettoSplit")}
        />

        <div className="perfetto-result">
          {result?.error ? (
            <Typography.Paragraph type="danger" className="perfetto-result-error">
              {result.error}
            </Typography.Paragraph>
          ) : null}

          {result?.summary ? (
            <SummaryBody summary={result.summary} packageName={result.package || ""} />
          ) : !busy && !loadingId && !result?.error ? (
            <div className="perfetto-empty">
              <Typography.Text type="secondary">
                {history.length > 0 ? t("device.perfettoPickHist") : t("device.perfettoEmpty")}
              </Typography.Text>
            </div>
          ) : busy || loadingId ? (
            <div className="perfetto-empty">
              <Typography.Text type="secondary">{statusText}</Typography.Text>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
