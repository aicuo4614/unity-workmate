/**
 * 真机 Unity Profiler：通知 kabu-profiler 启停，pull .raw 和截图；左历史 / 右摘要。
 */
import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { useTranslation } from "react-i18next";
import { Button, Input, Spin, Typography } from "antd";
import {
  selectAndroidProfilerFrames,
  ANDROID_SERVER_DOWN,
  ANDROID_TIMEOUT,
  androidErrorText,
  androidProfilerDownloadUrl,
  androidProfilerShotUrl,
  convertAndroidProfilerAnalysis,
  fetchAndroidProfilerDetail,
  fetchAndroidProfilerHistory,
  fetchAndroidProfilerLatest,
  loadAndroidProfilerHistory,
  importAndroidProfiler,
  openAndroidProfilerInEditor,
  revealAndroidProfilerFolder,
  startAndroidProfiler,
  stopAndroidProfiler,
} from "../services/androidService";
import type {
  AndroidProfilerHistoryItem,
  AndroidProfilerResult,
  AndroidProfilerSummary,
  ProfilerDetailData,
  ProfilerDetailFrame,
  ProfilerDetailMarker,
} from "../services/types";

import { ProfilerCallTreeProvider, ProfilerMarkerCalls } from "./ProfilerCallTree";

type Props = {
  serial: string;
  connected: boolean;
  active: boolean;
  threadId: string;
};

const SPLIT_KEY = "ailearning.profiler.split.v1";
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

function fmtSize(n: number): string {
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
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

const HOT_FRAME_LIMIT = 15;

function markerMs(m: ProfilerDetailMarker): number {
  return Number(m.ms ?? m.total_ms ?? 0);
}

function fmtCount(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n) || n < 0) return "—";
  const v = Math.round(n);
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 10_000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

function hasRenderStats(frame: ProfilerDetailFrame): boolean {
  return frame.batches != null || frame.setpass != null || frame.triangles != null;
}

function markerTitle(name: string): string {
  const cut = name.split("::").pop() || name;
  return cut.replace(/\(\)$/, "");
}

function shotNumber(file: string): number {
  const m = /frame_(\d+)/i.exec(file);
  return m ? Number(m[1]) : -1;
}

/** 截图下标是开录后的游戏帧，Profiler 缓冲通常只留末尾。用最后一帧对齐最后一张图。 */
function shotForFrame(
  frame: number,
  lastProfiler: number,
  shots: string[],
): string | null {
  const indexed = shots
    .map((file) => ({ file, n: shotNumber(file) }))
    .filter((item) => item.n >= 0)
    .sort((a, b) => a.n - b.n);
  if (!indexed.length) return null;
  const lastShot = indexed[indexed.length - 1].n;
  const target = lastShot - (lastProfiler - frame);
  let best = indexed[0];
  let bestDist = Math.abs(best.n - target);
  for (const item of indexed) {
    const dist = Math.abs(item.n - target);
    if (dist < bestDist) {
      best = item;
      bestDist = dist;
    }
  }
  return best.file;
}

type FrameListKind =
  | "cpu"
  | "gpu"
  | "calls"
  | "gc"
  | "batches"
  | "drawcalls"
  | "setpass"
  | "tris"
  | "verts";

function rankByCpu(frames: ProfilerDetailFrame[]): ProfilerDetailFrame[] {
  return [...frames]
    .sort((a, b) => Number(b.cpu_ms || 0) - Number(a.cpu_ms || 0))
    .slice(0, HOT_FRAME_LIMIT);
}

function rankByGpu(frames: ProfilerDetailFrame[]): ProfilerDetailFrame[] {
  return [...frames]
    .filter((f) => Number(f.gpu_ms || 0) > 0.05)
    .sort((a, b) => Number(b.gpu_ms || 0) - Number(a.gpu_ms || 0))
    .slice(0, HOT_FRAME_LIMIT);
}

function rankByCalls(frames: ProfilerDetailFrame[]): ProfilerDetailFrame[] {
  return [...frames]
    .filter((f) => Number(f.calls_total || 0) > 0)
    .sort((a, b) => Number(b.calls_total || 0) - Number(a.calls_total || 0))
    .slice(0, HOT_FRAME_LIMIT);
}

function rankByCount(
  frames: ProfilerDetailFrame[],
  key: "batches" | "draw_calls" | "setpass" | "triangles" | "vertices",
): ProfilerDetailFrame[] {
  return [...frames]
    .filter((f) => {
      const n = Number(f[key]);
      return Number.isFinite(n) && n >= 0;
    })
    .sort((a, b) => Number(b[key] || 0) - Number(a[key] || 0))
    .slice(0, HOT_FRAME_LIMIT);
}

function rankByGc(frames: ProfilerDetailFrame[]): ProfilerDetailFrame[] {
  return [...frames]
    .filter((f) => Number(f.gc_alloc_bytes || 0) > 0)
    .sort((a, b) => Number(b.gc_alloc_bytes || 0) - Number(a.gc_alloc_bytes || 0))
    .slice(0, HOT_FRAME_LIMIT);
}

function fmtBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  if (n < 1024) return `${Math.round(n)} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function DetailOverview({
  threadId,
  detail,
  captureId,
  shots,
  locatingFrame,
  onLocate,
  onFrameOpen,
}: {
  threadId: string;
  detail: ProfilerDetailData;
  captureId: string;
  shots: string[];
  locatingFrame: number | null;
  onLocate: (frame: number) => void;
  onFrameOpen: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const overview = (detail.overview || {}) as Record<string, unknown>;
  const frames = Array.isArray(detail.frames) ? detail.frames : [];
  const [customInput, setCustomInput] = useState((detail.custom_frame_numbers || []).join(", "));
  const [customNumbers, setCustomNumbers] = useState(detail.custom_frame_numbers || []);
  const [customBusy, setCustomBusy] = useState(false);
  const [customError, setCustomError] = useState("");
  async function generateCustom() {
    const parts = customInput.trim().split(/[,，;；\s]+/);
    const numbers = [...new Set(parts.map(Number))];
    if (!customInput.trim() || parts.some(p => !/^\d+$/.test(p)) || numbers.some(n => !Number.isSafeInteger(n)) || numbers.length > 100) {
      setCustomError(t("device.profilerCustomInvalid"));
      return;
    }
    setCustomBusy(true);
    setCustomError("");
    try {
      const data = await selectAndroidProfilerFrames(captureId, threadId, numbers);
      setCustomNumbers(data.frames);
    } catch (e) {
      setCustomError(androidErrorText(e, t, "device.profilerConvertFail"));
    } finally {
      setCustomBusy(false);
    }
  }
  const cpuFrames = rankByCpu(frames);
  const gpuFramesRanked = rankByGpu(frames);
  const callFrames = rankByCalls(frames);
  const gcFrames = rankByGc(frames);
  const hasExtraTops = frames.some(
    (f) => Number(f.calls_total || 0) > 0 || Number(f.gc_alloc_bytes || 0) > 0,
  );
  const cpu = detail.cpu || {};
  const wait = detail.wait || {};
  // 换一份详情时回到列表；点进某一帧才离开列表。任意 Top 共用同一份帧详情。
  const [opened, setOpened] = useState<{ owner: ProfilerDetailData; frame: number } | null>(
    null,
  );
  const openFrameNo = opened && opened.owner === detail ? opened.frame : null;
  const openFrame = frames.find((f) => f.frame === openFrameNo) || null;
  const frameCount = Number(overview.frame_count || frames.length || 0);
  const avgCpu = Number(overview.avg_cpu_ms ?? overview.avg_frame_ms ?? 0);
  const avgGpu = Number(overview.avg_gpu_ms || 0);
  const gpuFrames = Number(overview.gpu_frames || 0);
  const sessionTops = (cpu.top_markers || []).slice(0, 6);
  const sessionWait = (wait.top_markers || []).slice(0, 4);
  const lastProfiler = Number(overview.last_frame ?? frames[frames.length - 1]?.frame ?? 0);

  useEffect(() => {
    onFrameOpen(openFrame != null);
    return () => onFrameOpen(false);
  }, [openFrame, onFrameOpen]);

  function openByFrame(frame: number) {
    setOpened({ owner: detail, frame });
  }

  return (
    <div className="profiler-aidata">
      {openFrame ? (
        <FramePage
          key={`${captureId}/${openFrame.frame}`}
          threadId={threadId}
          frame={openFrame}
          captureId={captureId}
          shot={shotForFrame(openFrame.frame, lastProfiler, shots)}
          locating={locatingFrame === openFrame.frame}
          locateDisabled={locatingFrame != null}
          onBack={() => setOpened(null)}
          onLocate={() => onLocate(openFrame.frame)}
        />
      ) : (
        <>
          <div className="profiler-areas-title">{t("device.profilerHasDetail")}</div>
          <div className="profiler-kv-row">
            <span>{t("device.profilerFrames", { n: frameCount })}</span>
            <span>
              {t("device.profilerCpuAvg")} <strong>{avgCpu.toFixed(1)} ms</strong>
            </span>
            <span>
              {t("device.profilerGpuAvg")} <strong>{avgGpu.toFixed(1)} ms</strong>
            </span>
            <span>
              p95 <strong>{Number(overview.p95_frame_ms || 0).toFixed(1)} ms</strong>
            </span>
            <span>
              p99 <strong>{Number(overview.p99_frame_ms || 0).toFixed(1)} ms</strong>
            </span>
            <span>
              over <strong>{Number(overview.over_budget_pct || 0).toFixed(0)}%</strong>
            </span>
          </div>
          {gpuFrames <= 0 ? (
            <p className="profiler-aidata-hint">{t("device.profilerGpuMissing")}</p>
          ) : null}

          <details className="profiler-top-section" open>
            <summary>{t("device.profilerCustomFrames")}</summary>
            <p className="profiler-aidata-hint">{t("device.profilerCustomHint")}</p>
            <Input.TextArea value={customInput} onChange={e => setCustomInput(e.target.value)}
              placeholder={t("device.profilerCustomPlaceholder")} autoSize={{ minRows: 1, maxRows: 4 }} disabled={customBusy} />
            <Button size="small" loading={customBusy} onClick={() => void generateCustom()}>
              {t("device.profilerCustomGenerate")}
            </Button>
            {customError ? <Typography.Paragraph type="danger">{customError}</Typography.Paragraph> : null}
            <ul className="profiler-frame-list">
              {customNumbers.map((n, i) => {
                const frame = frames.find(f => f.frame === n);
                return frame ? <FrameCard key={n} rank={i + 1} frame={frame} kind="cpu"
                  captureId={captureId} shot={shotForFrame(n, lastProfiler, shots)} onOpen={() => openByFrame(n)} /> : null;
              })}
            </ul>
          </details>

          {cpuFrames.length ? (
            <FrameTopSection
              title={t("device.profilerTopFrames")}
              hint={t("device.profilerTopFramesHint")}
              frames={cpuFrames}
              kind="cpu"
              captureId={captureId}
              lastProfiler={lastProfiler}
              shots={shots}
              onOpen={openByFrame}
            />
          ) : (
            <Typography.Paragraph type="secondary" className="profiler-notes">
              {t("device.profilerNeedTopFrames")}
            </Typography.Paragraph>
          )}

          <FrameTopSection
            title={t("device.profilerTopGpu")}
            hint={t("device.profilerTopGpuHint")}
            frames={gpuFramesRanked}
            kind="gpu"
            captureId={captureId}
            lastProfiler={lastProfiler}
            shots={shots}
            onOpen={openByFrame}
          />

          {(
            [
              ["batches", "batches", t("device.profilerTopBatches")],
              ["drawcalls", "draw_calls", t("device.profilerTopDrawCalls")],
              ["setpass", "setpass", t("device.profilerTopSetPass")],
              ["tris", "triangles", t("device.profilerTopTris")],
              ["verts", "vertices", t("device.profilerTopVerts")],
            ] as const
          ).map(([kind, key, title]) => (
            <FrameTopSection
              key={kind}
              title={title}
              hint={t("device.profilerTopCountHint")}
              frames={rankByCount(frames, key)}
              kind={kind}
              captureId={captureId}
              lastProfiler={lastProfiler}
              shots={shots}
              onOpen={openByFrame}
            />
          ))}

          {hasExtraTops ? (
            <>
              <FrameTopSection
                title={t("device.profilerTopCalls")}
                hint={t("device.profilerTopCallsHint")}
                frames={callFrames}
                kind="calls"
                captureId={captureId}
                lastProfiler={lastProfiler}
                shots={shots}
                onOpen={openByFrame}
              />
              <FrameTopSection
                title={t("device.profilerTopGc")}
                hint={t("device.profilerTopGcHint")}
                frames={gcFrames}
                kind="gc"
                captureId={captureId}
                lastProfiler={lastProfiler}
                shots={shots}
                onOpen={openByFrame}
              />
            </>
          ) : frames.length ? (
            <p className="profiler-aidata-hint">{t("device.profilerTopNeedRegen")}</p>
          ) : null}

          {sessionTops.length ? (
            <details className="profiler-session-stats">
              <summary>{t("device.profilerSessionStats")}</summary>
              <div className="profiler-areas">
                <div className="profiler-areas-title">{t("device.profilerCpuTop")}</div>
                <MarkerList markers={sessionTops} mode="ms" />
              </div>
              {sessionWait.length ? (
                <div className="profiler-areas">
                  <div className="profiler-areas-title">{t("device.profilerWaitTop")}</div>
                  <MarkerList markers={sessionWait} mode="ms" />
                </div>
              ) : null}
            </details>
          ) : null}
        </>
      )}
    </div>
  );
}

function FrameTopSection({
  title,
  hint,
  frames,
  kind,
  captureId,
  lastProfiler,
  shots,
  onOpen,
}: {
  title: string;
  hint: string;
  frames: ProfilerDetailFrame[];
  kind: FrameListKind;
  captureId: string;
  lastProfiler: number;
  shots: string[];
  onOpen: (frame: number) => void;
}) {
  if (!frames.length) return null;
  return (
    <details className="profiler-top-section" open={kind === "cpu"}>
      <summary>
        {title}
        <span className="profiler-top-section-count">{frames.length}</span>
      </summary>
      <p className="profiler-aidata-hint">{hint}</p>
      <ul className="profiler-frame-list">
        {frames.map((f, index) => (
          <FrameCard
            key={`${kind}-${f.frame}`}
            rank={index + 1}
            frame={f}
            kind={kind}
            captureId={captureId}
            shot={shotForFrame(f.frame, lastProfiler, shots)}
            onOpen={() => onOpen(f.frame)}
          />
        ))}
      </ul>
    </details>
  );
}

function FrameThumb({
  captureId,
  shot,
  large,
}: {
  captureId: string;
  shot: string | null;
  large?: boolean;
}) {
  const { t } = useTranslation();
  if (!captureId || !shot) {
    return (
      <span className={`profiler-frame-thumb is-empty${large ? " is-large" : ""}`}>
        {t("device.profilerFrameNoShot")}
      </span>
    );
  }
  return (
    <img
      className={`profiler-frame-thumb${large ? " is-large" : ""}`}
      src={androidProfilerShotUrl(captureId, shot)}
      alt=""
      loading="lazy"
    />
  );
}

function FrameCard({
  rank,
  frame,
  kind,
  captureId,
  shot,
  onOpen,
}: {
  rank: number;
  frame: ProfilerDetailFrame;
  kind: FrameListKind;
  captureId: string;
  shot: string | null;
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  const titleMarker =
    kind === "gpu"
      ? ""
      : kind === "calls"
        ? frame.calls_top?.[0]?.name
        : kind === "gc"
          ? frame.gc_top?.[0]?.name
          : frame.cpu_top?.[0]?.name;
  const topName =
    kind === "gpu"
      ? t("device.profilerFrameLabel", { frame: frame.frame })
      : titleMarker
        ? markerTitle(titleMarker)
        : t("device.profilerFrameDetail", {
            frame: frame.frame,
            ms: Number(frame.cpu_ms).toFixed(1),
          });
  const countKey =
    kind === "batches"
      ? frame.batches
      : kind === "drawcalls"
        ? frame.draw_calls
        : kind === "setpass"
          ? frame.setpass
          : kind === "tris"
            ? frame.triangles
            : kind === "verts"
              ? frame.vertices
              : null;
  const countName =
    kind === "batches"
      ? t("device.profilerStatBatches")
      : kind === "drawcalls"
        ? t("device.profilerStatDrawCalls")
        : kind === "setpass"
          ? t("device.profilerStatSetPass")
          : kind === "tris"
            ? t("device.profilerStatTris")
            : kind === "verts"
              ? t("device.profilerStatVerts")
              : "";
  const meta =
    countKey != null
      ? t("device.profilerFrameCardStat", {
          name: countName,
          n: fmtCount(countKey),
          cpu: Number(frame.cpu_ms).toFixed(1),
        })
      : kind === "gpu"
      ? t("device.profilerFrameCardGpu", {
          gpu: Number(frame.gpu_ms || 0).toFixed(1),
          cpu: Number(frame.cpu_ms).toFixed(1),
        })
      : kind === "calls"
      ? t("device.profilerFrameCardCalls", {
          n: fmtCount(frame.calls_total),
          cpu: Number(frame.cpu_ms).toFixed(1),
        })
      : kind === "gc"
        ? t("device.profilerFrameCardGc", {
            bytes: fmtBytes(Number(frame.gc_alloc_bytes || 0)),
            cpu: Number(frame.cpu_ms).toFixed(1),
          })
        : t("device.profilerFrameCardMeta", {
            cpu: Number(frame.cpu_ms).toFixed(1),
            gpu: Number(frame.gpu_ms || 0).toFixed(1),
          });
  return (
    <li>
      <button type="button" className="profiler-frame-card" onClick={onOpen}>
        <span className="profiler-frame-rank">{t("device.profilerFrameRank", { n: rank })}</span>
        <FrameThumb captureId={captureId} shot={shot} />
        <span className="profiler-frame-card-body">
          <span className="profiler-frame-card-name">{topName}</span>
          <span className="profiler-frame-card-meta">
            #{frame.frame}
            {" · "}
            {meta}
          </span>
          {kind === "cpu" && hasRenderStats(frame) ? (
            <span className="profiler-frame-card-meta">
              {t("device.profilerFrameRender", {
                batches: fmtCount(frame.batches),
                drawcalls: fmtCount(frame.draw_calls),
                setpass: fmtCount(frame.setpass),
                tris: fmtCount(frame.triangles),
                verts: fmtCount(frame.vertices),
              })}
            </span>
          ) : null}
        </span>
      </button>
    </li>
  );
}

function FramePage({
  threadId,
  frame,
  captureId,
  shot,
  locating,
  locateDisabled,
  onBack,
  onLocate,
}: {
  threadId: string;
  frame: ProfilerDetailFrame;
  captureId: string;
  shot: string | null;
  locating: boolean;
  locateDisabled: boolean;
  onBack: () => void;
  onLocate: () => void;
}) {
  const { t } = useTranslation();
  const gpu = Number(frame.gpu_ms || 0);
  const title = frame.cpu_top?.[0]?.name
    ? markerTitle(frame.cpu_top[0].name)
    : t("device.profilerFrameDetail", {
        frame: frame.frame,
        ms: Number(frame.cpu_ms).toFixed(1),
      });
  return (
    <ProfilerCallTreeProvider name={captureId} threadId={threadId} frame={frame.frame} available={!!frame.call_tree_available}>
    <div className="profiler-frame-page">
      <button type="button" className="profiler-frame-back" onClick={onBack}>
        {t("device.profilerFrameBack")}
      </button>
      <FrameThumb captureId={captureId} shot={shot} large />
      <div className="profiler-areas-title">{title}</div>
      <div className="profiler-frame-render">
        {t("device.profilerFrameDetail", {
          frame: frame.frame,
          ms: Number(frame.cpu_ms).toFixed(1),
        })}
        {gpu > 0.05 ? ` · ${t("device.profilerFrameGpu", { ms: gpu.toFixed(1) })}` : ""}
      </div>
      <button
        type="button"
        className="profiler-frame-locate"
        title={t("device.profilerLocateHint")}
        disabled={locateDisabled}
        onClick={onLocate}
      >
        {locating ? t("device.profilerLocating") : t("device.profilerLocateFrame")}
      </button>
      {hasRenderStats(frame) ? (
        <>
          <div className="profiler-frame-render">
            {t("device.profilerFrameRender", {
              batches: fmtCount(frame.batches),
              drawcalls: fmtCount(frame.draw_calls),
              setpass: fmtCount(frame.setpass),
              tris: fmtCount(frame.triangles),
              verts: fmtCount(frame.vertices),
            })}
          </div>
          <div className="profiler-frame-render">
            {t("device.profilerFrameRenderExtra", {
              shadows: fmtCount(frame.shadow_casters),
              instanced: fmtCount(frame.instanced_batches),
              post: Number(frame.post_ms || 0).toFixed(1),
            })}
          </div>
        </>
      ) : (
        <p className="profiler-aidata-hint">{t("device.profilerFrameRenderMissing")}</p>
      )}
      {(frame.cpu_self_top || []).length ? (
        <>
          <details className="profiler-top-section" open>
            <summary>{t("device.profilerFrameCpuTimeTop")}</summary>
            <p className="profiler-aidata-hint">{t("device.profilerFrameCpuTimeHint")}</p>
            <MarkerList markers={(frame.cpu_top || []).slice(0, 12)} mode="ms" />
          </details>
          <details className="profiler-top-section" open>
            <summary>{t("device.profilerFrameCpuSelfTop")}</summary>
            <p className="profiler-aidata-hint">{t("device.profilerFrameCpuSelfHint")}</p>
            <MarkerList markers={(frame.cpu_self_top || []).slice(0, 12)} mode="ms" />
          </details>
        </>
      ) : (frame.cpu_top || []).length ? (
        <details className="profiler-top-section" open>
          <summary>{t("device.profilerFrameCpuTop")}</summary>
          <p className="profiler-aidata-hint">{t("device.profilerFrameCpuLegacyHint")}</p>
          <MarkerList markers={(frame.cpu_top || []).slice(0, 12)} mode="ms" />
        </details>
      ) : null}
      {(frame.calls_top || []).length ? (
        <details className="profiler-top-section" open>
          <summary>{t("device.profilerFrameCallsTop")}</summary>
          <p className="profiler-aidata-hint">{t("device.profilerFrameCallsHint")}</p>
          <MarkerList markers={(frame.calls_top || []).slice(0, 12)} mode="hits" />
        </details>
      ) : null}
      {(frame.gc_top || []).length ? (
        <details className="profiler-top-section" open>
          <summary>{t("device.profilerFrameGcTop")}</summary>
          <p className="profiler-aidata-hint">{t("device.profilerFrameGcHint")}</p>
          <MarkerList markers={(frame.gc_top || []).slice(0, 12)} mode="bytes" />
        </details>
      ) : null}
      {(frame.wait_top || []).length ? (
        <details className="profiler-top-section">
          <summary>{t("device.profilerFrameWaitTop")}</summary>
          <MarkerList markers={(frame.wait_top || []).slice(0, 8)} mode="ms" />
        </details>
      ) : null}
    </div>
    </ProfilerCallTreeProvider>
  );
}

function MarkerList({
  markers,
  mode,
}: {
  markers: ProfilerDetailMarker[];
  mode: "ms" | "hits" | "bytes";
}) {
  const { t } = useTranslation();
  return (
    <ul className="profiler-aidata-tops">
      {markers.map((m) => {
        let value = "";
        if (mode === "hits") {
          // Calls：次数 + 本帧该函数 Self 累计（总耗时）；小数也要标，别用 0.05 门槛藏掉
          const ms = markerMs(m);
          const msText = ms >= 1 ? ms.toFixed(1) : ms.toFixed(3);
          value = t("device.profilerMarkerHitsMs", {
            n: m.hits || 0,
            ms: msText,
          });
        } else if (mode === "bytes") {
          value = fmtBytes(Number(m.bytes || 0));
          if (m.hits && m.hits > 1) value += ` · ${t("device.profilerMarkerHits", { n: m.hits })}`;
        } else {
          value = `${markerMs(m).toFixed(1)} ms`;
          if (m.hits && m.hits > 1) value += ` · ${t("device.profilerMarkerHits", { n: m.hits })}`;
        }
        return (
          <li key={m.name} className="profiler-marker-row">
            <ProfilerMarkerCalls name={m.name} value={value} />
          </li>
        );
      })}
    </ul>
  );
}

function ShotStrip({ result }: { result: AndroidProfilerResult }) {
  const { t } = useTranslation();
  const shots = result.shots || [];
  const name = result.id || "";
  if (!shots.length || !name) return null;
  const shown = shots.slice(0, 48);
  const title = `${t("device.profilerShots")}${
    shots.length > shown.length ? ` ${shown.length}/${shots.length}` : ""
  }`;
  // 默认收起：这不是热点帧，展开 48 张会把下面的帧列表顶出去。
  return (
    <details className="profiler-shots">
      <summary>{title}</summary>
      <p className="profiler-aidata-hint">{t("device.profilerShotsHint")}</p>
      <div className="profiler-shot-strip">
        {shown.map((file) => (
          <figure key={file} className="profiler-shot">
            <img src={androidProfilerShotUrl(name, file)} alt={file} loading="lazy" />
            <figcaption>{file.replace(/^frame_/, "").replace(/\.jpg$/i, "")}</figcaption>
          </figure>
        ))}
      </div>
    </details>
  );
}

function SummaryBody({
  threadId,
  summary,
  result,
  detail,
  detailLoading,
  detailErr,
  opening,
  converting,
  locatingFrame,
  openMsg,
  openErr,
  onOpenEditor,
  onLocate,
  onConvert,
  onOpenFolder,
  frameOpen,
  onFrameOpen,
}: {
  threadId: string;
  summary: AndroidProfilerSummary;
  result: AndroidProfilerResult;
  detail: ProfilerDetailData | null;
  detailLoading: boolean;
  detailErr: string;
  opening: boolean;
  converting: boolean;
  locatingFrame: number | null;
  openMsg: string;
  openErr: string;
  onOpenEditor: () => void;
  onLocate: (frame: number) => void;
  onConvert: () => void;
  onOpenFolder: () => void;
  frameOpen: boolean;
  onFrameOpen: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const canOpen = !!(result.id || result.local_path) && result.status === "done";
  const hasDetail = !!result.detaildata_path;
  return (
    <div className="profiler-summary">
      {!frameOpen ? (
        <>
      <div className="profiler-kv-row">
        <span>
          {t("device.profilerMagic")}:{" "}
          <strong>
            {summary.valid_magic
              ? t("device.profilerMagicOk")
              : t("device.profilerMagicBad")}
          </strong>
        </span>
        <span>
          {t("device.profilerSize")}:{" "}
          <strong>{fmtSize(summary.size_bytes || result.size_bytes)}</strong>
        </span>
        {summary.unity_version ? (
          <span>
            {t("device.profilerVersion")}: <strong>{summary.unity_version}</strong>
          </span>
        ) : null}
      </div>
      <div className="profiler-open-row">
        <Button
          size="small"
          type="primary"
          loading={converting}
          disabled={!canOpen || converting || opening}
          onClick={onConvert}
        >
          {converting ? t("device.profilerConverting") : t("device.profilerConvertBtn")}
        </Button>
        <Button size="small" disabled={!canOpen} onClick={onOpenFolder}>
          {t("device.profilerOpenFolder")}
        </Button>
        <Button
          size="small"
          loading={opening}
          disabled={!canOpen || opening || converting}
          onClick={onOpenEditor}
        >
          {opening ? t("device.profilerOpening") : t("device.profilerOpenBtn")}
        </Button>
        <Typography.Text type="secondary" className="profiler-open-hint">
          {hasDetail ? t("device.profilerHasDetail") : t("device.profilerNeedConvert")}
        </Typography.Text>
      </div>
      {openErr ? (
        <Typography.Paragraph type="danger" className="profiler-notes">
          {openErr}
        </Typography.Paragraph>
      ) : null}
      {openMsg ? (
        <Typography.Paragraph type="secondary" className="profiler-notes">
          {openMsg}
        </Typography.Paragraph>
      ) : null}
        </>
      ) : null}
      {detailLoading ? (
        <div className="profiler-empty">
          <Spin />
          <span>{t("device.profilerDetailLoading")}</span>
        </div>
      ) : null}
      {detailErr ? (
        <Typography.Paragraph type="danger" className="profiler-notes">
          {detailErr}
        </Typography.Paragraph>
      ) : null}
      {detail ? (
        <DetailOverview
          key={result.id}
          threadId={threadId}
          detail={detail}
          captureId={result.id || ""}
          shots={result.shots || []}
          locatingFrame={locatingFrame}
          onLocate={onLocate}
          onFrameOpen={onFrameOpen}
        />
      ) : !detailLoading && !hasDetail ? (
        <p className="profiler-aidata-hint">{t("device.profilerHotPending")}</p>
      ) : null}
      {!frameOpen ? (
        <>
      <ShotStrip result={result} />
      {result.data_path || result.detaildata_path ? (
        <div className="profiler-meta">
          {result.data_path ? (
            <div>
              .data: <code>{result.data_path}</code>
            </div>
          ) : null}
          {result.detaildata_path ? (
            <div>
              .detaildata: <code>{result.detaildata_path}</code>
            </div>
          ) : null}
        </div>
      ) : null}
      {summary.areas?.length ? (
        <div className="profiler-areas">
          <div className="profiler-areas-title">{t("device.profilerAreas")}</div>
          <div className="profiler-area-chips">
            {summary.areas.map((a) => (
              <span key={a} className="profiler-chip">
                {a}
              </span>
            ))}
          </div>
        </div>
      ) : null}
      {summary.notes?.length ? (
        <Typography.Paragraph type="secondary" className="profiler-notes">
          {summary.notes.join(" · ")}
        </Typography.Paragraph>
      ) : null}
        </>
      ) : null}
    </div>
  );
}

export function ProfilerPanel({ serial, connected, active, threadId }: Props) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const [loadingId, setLoadingId] = useState("");
  const [opening, setOpening] = useState(false);
  const [converting, setConverting] = useState(false);
  const [openMsg, setOpenMsg] = useState("");
  const [openErr, setOpenErr] = useState("");
  const [result, setResult] = useState<AndroidProfilerResult | null>(null);
  const [detail, setDetail] = useState<ProfilerDetailData | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailErr, setDetailErr] = useState("");
  const [locatingFrame, setLocatingFrame] = useState<number | null>(null);
  const [frameOpen, setFrameOpen] = useState(false);
  const [history, setHistory] = useState<AndroidProfilerHistoryItem[]>([]);
  const [histError, setHistError] = useState("");
  const [histLoading, setHistLoading] = useState(false);
  const [splitW, setSplitW] = useState(readSplitWidth);
  const bodyRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);
  const splitLiveRef = useRef(splitW);

  async function refreshHistory() {
    setHistLoading(true);
    const tryOnce = () => fetchAndroidProfilerHistory(40, threadId);
    try {
      let data;
      try {
        data = await tryOnce();
      } catch (e) {
        // 进页时若刚好撞上 uvicorn reload，先别亮红字，短等再拉一次
        const msg = e instanceof Error ? e.message : "";
        if (msg !== ANDROID_SERVER_DOWN && msg !== ANDROID_TIMEOUT) throw e;
        await new Promise((r) => window.setTimeout(r, 800));
        data = await tryOnce();
      }
      if (data.ok) {
        setHistory(data.items || []);
        setHistError("");
      } else {
        setHistory([]);
        setHistError(data.error || t("device.profilerHistFail"));
      }
    } catch (e) {
      setHistory([]);
      setHistError(androidErrorText(e, t, "device.profilerHistFail"));
    } finally {
      setHistLoading(false);
    }
  }

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setResult(null);
    void fetchAndroidProfilerLatest(threadId)
      .then((data) => {
        if (
          !cancelled &&
          (data.status === "done" ||
            data.status === "recording" ||
            data.status === "pulling" ||
            data.status === "converting" ||
            data.summary)
        ) {
          setResult(data);
        }
      })
      .catch(() => {
        /* ignore */
      });
    void refreshHistory();
    return () => {
      cancelled = true;
    };
  }, [active, threadId]);

  const recording =
    result?.status === "recording" ||
    result?.status === "pulling" ||
    result?.status === "converting";

  useEffect(() => {
    if (!active || (result?.status !== "pulling" && result?.status !== "converting")) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void fetchAndroidProfilerLatest(threadId)
        .then((data) => {
          if (cancelled) return;
          setResult(data);
          void refreshHistory();
        })
        .catch(() => {
          /* 下一拍再问 */
        });
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [active, result?.status, threadId]);

  // 详情是全帧 JSON，不跟 latest 轮询走，只在这条采样的文件变了时拉一次
  useEffect(() => {
    const id = result?.id || "";
    const path = result?.detaildata_path || "";
    if (!active || !id || !path) {
      setDetail(null);
      setDetailErr("");
      setDetailLoading(false);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailErr("");
    void fetchAndroidProfilerDetail(id, threadId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((e) => {
        if (cancelled) return;
        setDetail(null);
        setDetailErr(androidErrorText(e, t, "device.profilerDetailFail"));
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [active, result?.id, result?.detaildata_path, threadId, t]);

  async function onImport(file: File) {
    if (busy || recording || converting || opening || loadingId || !threadId) return;
    setBusy(true);
    setImporting(true);
    setImportError("");
    setOpenMsg("");
    setOpenErr("");
    try {
      const data = await importAndroidProfiler(file, threadId);
      setResult(data);
      setFrameOpen(false);
      await refreshHistory();
    } catch (e) {
      setImportError(androidErrorText(e, t, "device.profilerImportFail"));
    } finally {
      setImporting(false);
      setBusy(false);
    }
  }

  async function onStart() {
    if (!connected || !serial || busy || recording) return;
    setBusy(true);
    try {
      const data = await startAndroidProfiler({ serial, thread_id: threadId });
      setResult(data);
    } catch (e) {
      setResult({
        ok: false,
        error: androidErrorText(e, t, "device.profilerFail"),
        status: "error",
        serial,
        package: "",
        activity: "",
        remote_path: "",
        local_path: "",
        started_at: "",
        finished_at: "",
        size_bytes: 0,
        thread_id: threadId,
      });
    } finally {
      setBusy(false);
    }
  }

  async function onStop() {
    if (busy) return;
    setBusy(true);
    try {
      const data = await stopAndroidProfiler();
      setResult(data);
      await refreshHistory();
    } catch (e) {
      setResult({
        ok: false,
        error: androidErrorText(e, t, "device.profilerFail"),
        status: "error",
        serial,
        package: result?.package || "",
        activity: result?.activity || "",
        remote_path: result?.remote_path || "",
        local_path: "",
        started_at: result?.started_at || "",
        finished_at: "",
        size_bytes: 0,
        thread_id: threadId,
      });
    } finally {
      setBusy(false);
    }
  }

  async function onOpenHistory(id: string) {
    if (!id || busy || loadingId || recording) return;
    setLoadingId(id);
    setOpenMsg("");
    setOpenErr("");
    try {
      const data = await loadAndroidProfilerHistory(id, threadId);
      setResult(data);
    } catch (e) {
      setResult({
        ok: false,
        error: androidErrorText(e, t, "device.profilerFail"),
        status: "error",
        serial: "",
        package: "",
        activity: "",
        remote_path: "",
        local_path: "",
        started_at: "",
        finished_at: "",
        size_bytes: 0,
        id,
        thread_id: threadId,
      });
    } finally {
      setLoadingId("");
    }
  }

  async function onOpenInEditor(frame = -1) {
    if (!result || opening || converting || busy || recording || locatingFrame != null) return;
    const id = result.id || "";
    if (!id && !result.local_path) return;
    if (frame >= 0) setLocatingFrame(frame);
    else setOpening(true);
    setOpenMsg("");
    setOpenErr("");
    try {
      const data = await openAndroidProfilerInEditor(id, threadId, frame);
      const matchTip =
        data.match === "exact"
          ? t("device.profilerMatchExact")
          : data.match === "minor"
            ? t("device.profilerMatchMinor")
            : data.match === "fallback"
              ? t("device.profilerMatchFallback")
              : t("device.profilerMatchNone");
      const parts = [
        data.launched ? t("device.profilerOpenOk") : "",
        data.unity_version
          ? `${t("device.profilerVersion")} ${data.unity_version}`
          : "",
        data.matched_version
          ? `${t("device.profilerMatched")} ${data.matched_version}（${matchTip}）`
          : matchTip,
        data.hint || "",
      ].filter(Boolean);
      if (data.ok) {
        setOpenMsg(parts.join(" · "));
        if (data.error) setOpenErr(data.error);
      } else {
        setOpenErr(data.error || t("device.profilerOpenFail"));
        if (data.hint) setOpenMsg(data.hint);
      }
    } catch (e) {
      setOpenErr(androidErrorText(e, t, "device.profilerOpenFail"));
    } finally {
      setOpening(false);
      setLocatingFrame(null);
    }
  }

  async function onOpenDataFolder() {
    if (!result) return;
    const id = result.id || "";
    if (!id && !result.local_path) return;
    setOpenMsg("");
    setOpenErr("");
    try {
      const data = await revealAndroidProfilerFolder(id, threadId);
      if (!data.ok) setOpenErr(data.error || t("device.profilerOpenFolderFail"));
    } catch (e) {
      setOpenErr(androidErrorText(e, t, "device.profilerOpenFolderFail"));
    }
  }

  async function onConvertAnalysis() {
    if (!result || converting || opening || busy || recording) return;
    const id = result.id || "";
    if (!id && !result.local_path) return;
    setConverting(true);
    setOpenMsg("");
    setOpenErr("");
    try {
      const data = await convertAndroidProfilerAnalysis(id, threadId, 1800);
      if (data.ok) {
        setOpenMsg(
          [
            t("device.profilerConvertOk"),
            data.frame_count
              ? t("device.profilerFrames", { n: data.frame_count })
              : "",
            data.hint || "",
          ]
            .filter(Boolean)
            .join(" · "),
        );
        // 重新 load 以带上 aidata 正文
        if (id) {
          const fresh = await loadAndroidProfilerHistory(id, threadId);
          setResult(fresh);
        } else {
          setResult({
            ...result,
            data_path: data.data_path,
            aidata_path: data.aidata_path,
          });
        }
        await refreshHistory();
      } else {
        setOpenErr(data.error || t("device.profilerConvertFail"));
        if (data.hint) setOpenMsg(data.hint);
      }
    } catch (e) {
      setOpenErr(androidErrorText(e, t, "device.profilerConvertFail"));
    } finally {
      setConverting(false);
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

  const canDownload = !!(result?.ok && (result.id || result.local_path) && result.status === "done");
  const downloadName = result?.id || "";
  const selectedId = result?.id || "";
  const statusText = importing ? t("device.profilerImporting") :
    result?.status === "pulling"
      ? t("device.profilerPulling")
      : result?.status === "converting"
        ? t("device.profilerConverting")
        : busy
        ? recording
          ? t("device.profilerStopping")
          : loadingId
            ? t("device.profilerLoadingHist")
            : t("device.profilerStarting")
        : result?.status === "recording"
          ? t("device.profilerRecording")
          : connected
            ? t("device.profilerHint")
            : t("device.profilerNeedConnect");

  return (
    <div className="profiler-panel">
      <div className="profiler-toolbar">
        <input ref={fileRef} type="file" accept=".raw" hidden onChange={(e) => {
          const file = e.currentTarget.files?.[0];
          e.currentTarget.value = "";
          if (file) void onImport(file);
        }} />
        <Button size="small" loading={importing}
          disabled={!threadId || busy || recording || converting || opening || !!loadingId}
          onClick={() => fileRef.current?.click()}>
          {t("device.profilerImport")}
        </Button>
        <Button
          size="small"
          type="primary"
          loading={busy && !recording && !importing}
          disabled={!connected || !serial || recording || busy || converting || opening || !!loadingId}
          onClick={() => void onStart()}
        >
          {t("device.profilerStart")}
        </Button>
        <Button
          size="small"
          danger
          loading={result?.status === "pulling" || (busy && recording)}
          disabled={result?.status !== "recording"}
          onClick={() => void onStop()}
        >
          {t("device.profilerStop")}
        </Button>
        <Button
          size="small"
          disabled={!canDownload}
          href={canDownload ? androidProfilerDownloadUrl(downloadName || undefined) : undefined}
          target="_blank"
        >
          {t("device.profilerDownload")}
        </Button>
        <Typography.Text type="secondary" className="profiler-status">
          {result?.status === "pulling" || result?.status === "converting" ? (
            <Spin size="small" />
          ) : null}
          {statusText}
        </Typography.Text>
      </div>

      <div
        ref={bodyRef}
        className="profiler-body"
        style={{ gridTemplateColumns: `${splitW}px 6px minmax(0, 1fr)` }}
      >
        <aside className="profiler-history">
          <div className="profiler-history-head">
            <span className="profiler-history-title">{t("device.profilerHistory")}</span>
            <button
              type="button"
              className="profiler-history-refresh"
              disabled={histLoading}
              onClick={() => void refreshHistory()}
            >
              {histLoading ? t("device.profilerHistLoading") : t("device.profilerHistRefresh")}
            </button>
          </div>
          {histError ? (
            <p className="profiler-history-msg is-error">{histError}</p>
          ) : history.length === 0 ? (
            <p className="profiler-history-msg">
              {histLoading ? t("device.profilerHistLoading") : t("device.profilerHistEmpty")}
            </p>
          ) : (
            <ul className="profiler-history-list">
              {history.map((item) => {
                const activeRow = item.id === selectedId;
                const meta = [
                  item.source === "import" ? t("device.profilerImported") : "",
                  item.original_name || item.package || item.serial || "",
                  fmtSize(item.size_bytes),
                  item.valid_magic ? "PD3U" : "",
                  item.has_detaildata ? t("device.profilerDetailBadge") : item.has_data ? "data" : "",
                ]
                  .filter(Boolean)
                  .join(" · ");
                return (
                  <li key={item.id} className={activeRow ? "is-active" : undefined}>
                    <button
                      type="button"
                      className="profiler-history-item"
                      disabled={!!loadingId || busy || recording || converting || opening}
                      onClick={() => void onOpenHistory(item.id)}
                    >
                      <span className="profiler-history-time">{fmtHistTime(item.mtime)}</span>
                      <span className="profiler-history-meta">
                        {meta || item.id}
                        {!item.has_summary ? ` · ${t("device.profilerHistNoSum")}` : ""}
                      </span>
                    </button>
                    <a
                      className="profiler-history-dl"
                      href={androidProfilerDownloadUrl(item.id)}
                      target="_blank"
                      rel="noreferrer"
                      title={t("device.profilerDownload")}
                    >
                      ↓
                    </a>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <div
          className="profiler-split"
          onPointerDown={onSplitDown}
          onPointerMove={onSplitMove}
          onPointerUp={onSplitUp}
          onPointerCancel={onSplitUp}
          role="separator"
          aria-orientation="vertical"
          aria-label={t("device.profilerSplit")}
        />

        <div className="profiler-result">
          {importError ? <Typography.Paragraph type="danger">{importError}</Typography.Paragraph> : null}
          {result?.error && !frameOpen ? (
            <Typography.Paragraph type="danger" className="profiler-error">
              {result.error}
            </Typography.Paragraph>
          ) : null}

          {result?.summary ? (
            <>
              {!frameOpen ? (
                <div className="profiler-meta">
                  {result.package ? (
                    <div>
                      {t("device.profilerPackage")}: <code>{result.package}</code>
                    </div>
                  ) : null}
                  {result.local_path ? (
                    <div>
                      {t("device.profilerLocal")}: <code>{result.local_path}</code>
                    </div>
                  ) : null}
                </div>
              ) : null}
              <SummaryBody
                threadId={threadId}
                summary={result.summary}
                result={result}
                detail={detail}
                detailLoading={detailLoading}
                detailErr={detailErr}
                opening={opening}
                converting={converting}
                locatingFrame={locatingFrame}
                openMsg={openMsg}
                openErr={openErr}
                onOpenEditor={() => void onOpenInEditor()}
                onLocate={(frame) => void onOpenInEditor(frame)}
                onConvert={() => void onConvertAnalysis()}
                onOpenFolder={() => void onOpenDataFolder()}
                frameOpen={frameOpen}
                onFrameOpen={setFrameOpen}
              />
            </>
          ) : result?.status === "recording" ||
            result?.status === "pulling" ||
            result?.status === "converting" ? (
            <div className="profiler-empty">
              {result.status === "pulling" || result.status === "converting" ? <Spin /> : null}
              <Typography.Text type="secondary">
                {result.status === "pulling"
                  ? t("device.profilerPulling")
                  : result.status === "converting"
                    ? t("device.profilerConverting")
                    : t("device.profilerRecording")}
              </Typography.Text>
              {result.package ? (
                <Typography.Text type="secondary" className="profiler-live-meta">
                  {t("device.profilerPackage")}: {result.package}
                </Typography.Text>
              ) : null}
            </div>
          ) : result && (result.package || result.local_path) ? (
            <div className="profiler-meta">
              {result.package ? (
                <div>
                  {t("device.profilerPackage")}: <code>{result.package}</code>
                </div>
              ) : null}
              {result.activity ? (
                <div>
                  {t("device.profilerActivity")}: <code>{result.activity}</code>
                </div>
              ) : null}
              {result.local_path ? (
                <div>
                  {t("device.profilerLocal")}: <code>{result.local_path}</code>
                  {result.size_bytes ? ` · ${fmtSize(result.size_bytes)}` : ""}
                </div>
              ) : null}
            </div>
          ) : !busy && !loadingId && !result?.error ? (
            <div className="profiler-empty">
              <Typography.Text type="secondary">
                {history.length > 0 ? t("device.profilerPickHist") : t("device.profilerEmpty")}
              </Typography.Text>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
