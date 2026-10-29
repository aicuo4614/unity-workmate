/** 后端 API 基址；开发时 Vite proxy 到 :8000 */
import { appConfig } from "../config";

export const API_BASE = appConfig.apiBase;

export type Health = {
  ok: boolean;
  mcp_ok: boolean;
  mcp_url: string;
  mcp_tool_count: number;
  mcp_error: string;
  /** RAG 预热是否完成 */
  rag_ready?: boolean;
  /** pending | loading | ready | error */
  rag_status?: string;
  rag_detail?: string;
};

export type AndroidDevice = {
  serial: string;
  state: string;
  model: string;
};

export type AndroidScan = {
  ok: boolean;
  error: string;
  devices: AndroidDevice[];
};

export type AndroidPanel = {
  ok: boolean;
  error: string;
  devices: AndroidDevice[];
  foreground: string;
  serial: string;
  loading: boolean;
};

export type AndroidPerfettoJankRow = {
  jank_type: string;
  count: number;
  pct: number;
  bucket: string;
};

export type AndroidPerfettoBlame = {
  app: number;
  sf: number;
  none: number;
  other: number;
};

export type AndroidPerfettoSummary = {
  frame_count: number;
  avg_frame_ms: number | null;
  p95_frame_ms: number | null;
  max_frame_ms: number | null;
  fps_est: number | null;
  jank_count: number | null;
  cpu_top: Array<{ name: string; cpu_ms: number }>;
  gpu_ms: number | null;
  gpu_note: string;
  gpu_counters_ok?: boolean;
  gpu_freq_mhz_avg?: number | null;
  gpu_freq_mhz_min?: number | null;
  gpu_freq_mhz_max?: number | null;
  gpu_temp_c_avg?: number | null;
  gpu_wait_count?: number;
  gpu_wait_total_ms?: number | null;
  gpu_wait_avg_ms?: number | null;
  gpu_wait_max_ms?: number | null;
  notes: string[];
  scope?: string;
  frame_source?: string;
  jank_over_16ms?: number | null;
  jank_typed_count?: number;
  jank_breakdown?: AndroidPerfettoJankRow[];
  blame?: AndroidPerfettoBlame;
  frame_series_ms?: number[];
  cpu_pkg_total_ms?: number | null;
  cpu_pkg_per_frame_ms?: number | null;
  cpu_pkg_note?: string;
};

export type AndroidPerfettoResult = {
  ok: boolean;
  error: string;
  status: string;
  serial: string;
  package: string;
  duration_sec: number;
  started_at: string;
  finished_at: string;
  trace_path: string;
  summary: AndroidPerfettoSummary | null;
  id?: string;
  thread_id?: string;
};

export type AndroidPerfettoHistoryItem = {
  id: string;
  trace_path: string;
  size_bytes: number;
  mtime: string;
  serial: string;
  package: string;
  duration_sec: number;
  fps_est: number | null;
  avg_frame_ms: number | null;
  has_summary: boolean;
  ok: boolean;
  thread_id?: string;
};

/** Unity Profiler .raw（kabu-profiler 插件写文件，面板再 pull） */
export type AndroidProfilerSummary = {
  valid_magic: boolean;
  size_bytes: number;
  unity_version?: string;
  areas: string[];
  notes: string[];
};

export type AndroidProfilerOpenResult = {
  ok: boolean;
  error: string;
  id: string;
  local_path: string;
  unity_version: string;
  matched_version: string;
  matched_exe: string;
  match: string;
  revealed: boolean;
  clipboard: boolean;
  launched: boolean;
  project_path: string;
  hint: string;
};

export type AndroidProfilerResult = {
  source?: string;
  original_name?: string;
  ok: boolean;
  error: string;
  status: string;
  serial: string;
  package: string;
  activity: string;
  remote_path: string;
  local_path: string;
  started_at: string;
  finished_at: string;
  size_bytes: number;
  thread_id?: string;
  id?: string;
  summary?: AndroidProfilerSummary | null;
  data_path?: string;
  detaildata_path?: string;
  aidata_path?: string;
  aidata?: Record<string, unknown> | null;
  shots?: string[];
};

export type AndroidProfilerHistoryItem = {
  source?: string;
  original_name?: string;
  id: string;
  local_path: string;
  size_bytes: number;
  mtime: string;
  serial: string;
  package: string;
  has_summary: boolean;
  has_data?: boolean;
  has_detaildata?: boolean;
  has_aidata?: boolean;
  ok: boolean;
  valid_magic: boolean;
  thread_id?: string;
};

export type ProfilerDetailMarker = {
  name: string;
  ms?: number;
  total_ms?: number;
  hits?: number;
  bytes?: number;
};

export type ProfilerCallNode = {
  parent: number;
  name: string;
  hits: number;
  total_ms: number;
  self_ms: number;
  bytes: number;
};
export type ProfilerCallTree = {
  schema: string;
  thread: string;
  frame: number;
  nodes: ProfilerCallNode[];
};

export type ProfilerDetailFrame = {
  call_tree_available?: boolean;
  frame: number;
  cpu_ms: number;
  gpu_ms?: number;
  wait_ms?: number;
  calls_total?: number;
  gc_alloc_bytes?: number;
  batches?: number;
  /** Rendering「Draw Calls Count」；旧 .detaildata 可能没有。 */
  draw_calls?: number;
  setpass?: number;
  triangles?: number;
  vertices?: number;
  shadow_casters?: number;
  instanced_batches?: number;
  post_ms?: number;
  cpu_top?: ProfilerDetailMarker[];
  /** Self ms 排行。旧的 .detaildata 没有这个字段。 */
  cpu_self_top?: ProfilerDetailMarker[];
  /** 按调用次数。 */
  calls_top?: ProfilerDetailMarker[];
  /** GC.Alloc 字节记到父函数。 */
  gc_top?: ProfilerDetailMarker[];
  wait_top?: ProfilerDetailMarker[];
};

export type ProfilerDetailData = {
  custom_frame_numbers?: number[];
  schema?: string;
  overview?: Record<string, unknown>;
  frames?: ProfilerDetailFrame[];
  cpu?: { top_markers?: ProfilerDetailMarker[] };
  wait?: { total_ms?: number; top_markers?: ProfilerDetailMarker[] };
};

export type AndroidProfilerConvertResult = {
  ok: boolean;
  error: string;
  id: string;
  local_path: string;
  data_path: string;
  detaildata_path?: string;
  aidata_path: string;
  unity_version: string;
  matched_version: string;
  match: string;
  frame_count: number;
  project_path?: string;
  hint: string;
};

export type SessionPrefs = {
  primary_metric: "batches" | "cpu_frame" | "post_process" | "overdraw" | "balanced";
  perceive_depth: "full" | "quick";
  platform_tone: "mobile_mid" | "mobile_high" | "editor_pc";
  scene_confirmed: boolean;
  scene_id: string;
  /** @deprecated 顾问扮演不用；兼容旧会话 */
  mode?: "advise_only" | "optimize";
  change_scope?:
    | "post_process"
    | "lighting"
    | "renderer_material"
    | "unrestricted_stepwise";
  visual_floor?: "preserve" | "allow_quality_drop" | "none";
  verify_enabled?: boolean;
};

export const defaultPrefs = (): SessionPrefs => ({
  primary_metric: "balanced",
  perceive_depth: "full",
  platform_tone: "editor_pc",
  scene_confirmed: false,
  scene_id: "",
  mode: "advise_only",
  change_scope: "unrestricted_stepwise",
  visual_floor: "preserve",
  verify_enabled: false,
});
