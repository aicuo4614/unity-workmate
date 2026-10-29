"""扮演注册表入口：Protocol + register/get；业务实现在 roles/scene_perf_*。"""

from server.roles.registry.context import build_session_context, register_role_context
from server.roles.registry.report import build_report, register_role_report
from server.roles.registry.stream import (
    register_role_stream,
    summarize_observation,
    summarize_tool_result,
)

__all__ = [
    "build_report",
    "build_session_context",
    "register_role_context",
    "register_role_report",
    "register_role_stream",
    "summarize_observation",
    "summarize_tool_result",
]
