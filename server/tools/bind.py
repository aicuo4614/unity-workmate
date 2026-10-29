"""分析站按扮演取一整台 MCP 的工具。不按工具名裁剪。

场景优化看 Unity，真机看 Android。同一台里的写入、点按也交给模型，由对话决定用不用。
"""
from __future__ import annotations

from server.tools.connect import ANDROID_SERVER, UNITY_SERVER, tools_of

# 未登记的扮演沿用 Unity。真机不看编辑器，所以单独指向 android-mcp。
_ROLE_SERVER = {
    "device_perf": ANDROID_SERVER,
}


def tools_for_analyze(role: str | None = None) -> list:
    server = _ROLE_SERVER.get(role or "", UNITY_SERVER)
    return tools_of(server)
