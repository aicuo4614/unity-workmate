"""MCP：连接、扮演绑定、真机侧栏。调用方仍写 `server.tools`。"""
from server.tools.android_panel import (
    android_panel,
    connect_android_panel,
    disconnect_android_panel,
    scan_devices,
)
from server.tools.bind import tools_for_analyze
from server.tools.connect import close_tools, init_tools, mcp_status, reconnect_mcp, tools
from server.tools.logcat_stream import clear_logcat, iter_logcat_sse
from server.tools.perfetto_capture import (
    capture_perfetto,
    latest_perfetto,
    latest_trace_file,
    list_perfetto_history,
    load_perfetto_history,
)
from server.tools.profiler_capture import (
    convert_profiler_analysis,
    latest_profiler,
    latest_profiler_file,
    list_profiler_history,
    load_profiler_history,
    open_profiler_in_editor,
    profiler_detaildata_file,
    profiler_calltree_file,
    reveal_profiler_data,
    profiler_shot_file,
    start_profiler_raw,
    stop_profiler_raw,
)

__all__ = [
    "android_panel",
    "capture_perfetto",
    "clear_logcat",
    "close_tools",
    "connect_android_panel",
    "convert_profiler_analysis",
    "disconnect_android_panel",
    "init_tools",
    "iter_logcat_sse",
    "latest_perfetto",
    "latest_profiler",
    "latest_profiler_file",
    "latest_trace_file",
    "list_perfetto_history",
    "list_profiler_history",
    "load_perfetto_history",
    "load_profiler_history",
    "mcp_status",
    "open_profiler_in_editor",
    "profiler_detaildata_file",
    "profiler_calltree_file",
    "profiler_shot_file",
    "reconnect_mcp",
    "reveal_profiler_data",
    "scan_devices",
    "start_profiler_raw",
    "stop_profiler_raw",
    "tools",
    "tools_for_analyze",
]
