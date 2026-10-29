import pydantic
from pydantic import Field
from typing import Any, Literal


class ChatMessage(pydantic.BaseModel):
    role: Literal["sytem", "user", "assistant"]
    content: str


class SessionPrefs(pydantic.BaseModel):
    """开局本局选项。"""

    primary_metric: Literal[
        "batches", "cpu_frame", "post_process", "overdraw", "balanced"
    ] = "balanced"
    # 旧客户端仍可能带上；采数不再按这一档切清单
    perceive_depth: Literal["full", "quick"] = "full"
    platform_tone: Literal["mobile_mid", "mobile_high", "editor_pc"] = "editor_pc"
    scene_confirmed: bool = False
    scene_id: str = ""
    # 兼容旧字段
    mode: Literal["advise_only", "optimize"] = "advise_only"
    change_scope: Literal[
        "post_process", "lighting", "renderer_material", "unrestricted_stepwise"
    ] = "unrestricted_stepwise"
    visual_floor: Literal["preserve", "allow_quality_drop", "none"] = "preserve"
    verify_enabled: bool = False


class ChatRequest(pydantic.BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    thread_id: str = "default"
    agent_role: str = "scene_perf"
    session_prefs: SessionPrefs | None = None


class ChatResponse(pydantic.BaseModel):
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class HealthResponse(pydantic.BaseModel):
    ok: bool
    mcp_ok: bool
    mcp_url: str = ""
    mcp_tool_count: int = 0
    mcp_error: str = ""
    # RAG 预热：pending|loading|ready|error
    rag_ready: bool = False
    rag_status: str = "pending"
    rag_detail: str = ""


class McpReconnectRequest(pydantic.BaseModel):
    url: str
    persist: bool = True


class McpReconnectResponse(pydantic.BaseModel):
    ok: bool
    mcp_ok: bool
    mcp_url: str = ""
    mcp_tool_count: int = 0
    mcp_error: str = ""


class AndroidDevice(pydantic.BaseModel):
    serial: str
    state: str
    model: str = ""


class AndroidDevicesResponse(pydantic.BaseModel):
    """扫描设备（不启 MCP），给下拉框选。"""

    ok: bool
    error: str = ""
    devices: list[AndroidDevice] = Field(default_factory=list)


class AndroidConnectRequest(pydantic.BaseModel):
    serial: str = Field(..., min_length=1, description="用户在下拉里选中的设备序列号")


class AndroidPanelResponse(pydantic.BaseModel):
    """已连接后的侧栏状态：当前机 + 前台包。"""

    ok: bool
    error: str = ""
    devices: list[AndroidDevice] = Field(default_factory=list)
    foreground: str = ""
    serial: str = ""


class AndroidLogcatClearRequest(pydantic.BaseModel):
    """清除 logcat 缓冲；serial 可空，空则用当前已连设备。"""

    serial: str = ""


class AndroidLogcatClearResponse(pydantic.BaseModel):
    ok: bool
    error: str = ""


class AndroidPerfettoCaptureRequest(pydantic.BaseModel):
    """Perfetto：录一段。serial/package 可空，空则用侧栏已连机与 dumpsys 前台。"""

    serial: str = ""
    duration_sec: int = Field(default=5, ge=3, le=30)
    package: str = ""
    thread_id: str = Field(default="", description="当前会话 thread_id，写入文件名并过滤历史")


class AndroidPerfettoCpuTop(pydantic.BaseModel):
    name: str
    cpu_ms: float


class AndroidPerfettoJankRow(pydantic.BaseModel):
    jank_type: str
    count: int
    pct: float
    bucket: str = "other"


class AndroidPerfettoBlame(pydantic.BaseModel):
    app: int = 0
    sf: int = 0
    none: int = 0
    other: int = 0


class AndroidPerfettoSummary(pydantic.BaseModel):
    frame_count: int = 0
    avg_frame_ms: float | None = None
    p95_frame_ms: float | None = None
    max_frame_ms: float | None = None
    fps_est: float | None = None
    jank_count: int | None = None
    cpu_top: list[AndroidPerfettoCpuTop] = Field(default_factory=list)
    gpu_ms: float | None = None
    gpu_note: str = ""
    gpu_counters_ok: bool = False
    gpu_freq_mhz_avg: float | None = None
    gpu_freq_mhz_min: float | None = None
    gpu_freq_mhz_max: float | None = None
    gpu_temp_c_avg: float | None = None
    gpu_wait_count: int = 0
    gpu_wait_total_ms: float | None = None
    gpu_wait_avg_ms: float | None = None
    gpu_wait_max_ms: float | None = None
    notes: list[str] = Field(default_factory=list)
    # V2 诊断
    scope: str = "all"
    frame_source: str = ""
    jank_over_16ms: int | None = None
    jank_typed_count: int = 0
    jank_breakdown: list[AndroidPerfettoJankRow] = Field(default_factory=list)
    blame: AndroidPerfettoBlame = Field(default_factory=AndroidPerfettoBlame)
    frame_series_ms: list[float] = Field(default_factory=list)
    cpu_pkg_total_ms: float | None = None
    cpu_pkg_per_frame_ms: float | None = None
    cpu_pkg_note: str = ""


class AndroidPerfettoResponse(pydantic.BaseModel):
    ok: bool
    error: str = ""
    status: str = "idle"
    serial: str = ""
    package: str = ""
    duration_sec: int = 0
    started_at: str = ""
    finished_at: str = ""
    trace_path: str = ""
    summary: AndroidPerfettoSummary | None = None
    id: str = ""
    thread_id: str = ""


class AndroidPerfettoHistoryItem(pydantic.BaseModel):
    id: str
    trace_path: str = ""
    size_bytes: int = 0
    mtime: str = ""
    serial: str = ""
    package: str = ""
    duration_sec: int = 0
    fps_est: float | None = None
    avg_frame_ms: float | None = None
    has_summary: bool = False
    ok: bool = False
    thread_id: str = ""


class AndroidPerfettoHistoryResponse(pydantic.BaseModel):
    ok: bool
    error: str = ""
    items: list[AndroidPerfettoHistoryItem] = Field(default_factory=list)


class AndroidPerfettoLoadRequest(pydantic.BaseModel):
    """按文件名打开一条历史 trace（basename，如 xxx.perfetto-trace）。"""

    id: str = Field(..., min_length=1)
    thread_id: str = ""


class AndroidProfilerStartRequest(pydantic.BaseModel):
    """通知已打开的 kabu-profiler 开始写 .raw，不冷启。"""

    serial: str = ""
    package: str = ""
    thread_id: str = ""


class AndroidProfilerSummary(pydantic.BaseModel):
    """V1 轻量窥视，非完整帧耗时解析。"""

    valid_magic: bool = False
    size_bytes: int = 0
    unity_version: str = ""
    areas: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AndroidProfilerResponse(pydantic.BaseModel):
    source: str = "capture"
    original_name: str = ""
    ok: bool
    error: str = ""
    status: str = "idle"
    serial: str = ""
    package: str = ""
    activity: str = ""
    remote_path: str = ""
    local_path: str = ""
    started_at: str = ""
    finished_at: str = ""
    size_bytes: int = 0
    thread_id: str = ""
    id: str = ""
    summary: AndroidProfilerSummary | None = None
    data_path: str = ""
    detaildata_path: str = ""
    aidata_path: str = ""
    aidata: dict | None = None
    shots: list[str] = Field(default_factory=list)


class AndroidProfilerHistoryItem(pydantic.BaseModel):
    source: str = "capture"
    original_name: str = ""
    id: str
    local_path: str = ""
    size_bytes: int = 0
    mtime: str = ""
    serial: str = ""
    package: str = ""
    has_summary: bool = False
    has_data: bool = False
    has_detaildata: bool = False
    has_aidata: bool = False
    ok: bool = False
    valid_magic: bool = False
    thread_id: str = ""


class AndroidProfilerHistoryResponse(pydantic.BaseModel):
    ok: bool
    error: str = ""
    items: list[AndroidProfilerHistoryItem] = Field(default_factory=list)


class AndroidProfilerLoadRequest(pydantic.BaseModel):
    id: str = Field(..., min_length=1)
    thread_id: str = ""


class AndroidProfilerOpenRequest(pydantic.BaseModel):
    """用本机 Unity Profiler 打开一条 .raw（按录制版本匹配 Hub Editor）。

    frame>=0 时把 Profiler 光标移到该帧。
    """

    id: str = ""
    thread_id: str = ""
    frame: int = -1


class AndroidProfilerOpenResponse(pydantic.BaseModel):
    ok: bool
    error: str = ""
    id: str = ""
    local_path: str = ""
    unity_version: str = ""
    matched_version: str = ""
    matched_exe: str = ""
    match: str = "none"  # exact | minor | none
    revealed: bool = False
    clipboard: bool = False
    launched: bool = False
    project_path: str = ""
    hint: str = ""


class AndroidProfilerRevealRequest(pydantic.BaseModel):
    """在资源管理器中打开这条录制所在的 profiler 目录。"""

    id: str = ""
    thread_id: str = ""


class AndroidProfilerRevealResponse(pydantic.BaseModel):
    ok: bool
    error: str = ""
    path: str = ""


class AndroidProfilerConvertRequest(pydantic.BaseModel):
    """一键 .raw → .data + .detaildata，再裁 .aidata。"""

    id: str = ""
    thread_id: str = ""
    timeout_sec: float = 600


class AndroidProfilerConvertResponse(pydantic.BaseModel):
    ok: bool
    error: str = ""
    id: str = ""
    local_path: str = ""
    data_path: str = ""
    detaildata_path: str = ""
    aidata_path: str = ""
    unity_version: str = ""
    matched_version: str = ""
    match: str = "none"
    frame_count: int = 0
    project_path: str = ""
    hint: str = ""


class ResumeRequest(pydantic.BaseModel):
    thread_id: str = "default"
    action: Literal[
        "say",
        "ask",
        "approve",
        "skip",
        "reject",
    ] = Field(
        ...,
        description=(
            "hitl 闸门：say|ask + content；"
            "act 前：approve|skip；亦可 say 改口去分析"
        ),
    )
    step_index: int = 0
    content: str = ""


class ThreadGateResponse(pydantic.BaseModel):
    waiting: bool = False
    gate: Literal["hitl", "act"] | None = None
    need_clarify: bool = False
    need_approval: bool = False
    plan_summary: str = ""
    phase: str | None = None


class AndroidProfilerFramesRequest(pydantic.BaseModel):
    id: str = pydantic.Field(min_length=1)
    thread_id: str = pydantic.Field(min_length=1)
    frames: list[pydantic.StrictInt] = pydantic.Field(min_length=1, max_length=100)
