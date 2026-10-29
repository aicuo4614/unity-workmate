"""device_perf：流式摘要。界面表不整段进对话流。"""
from __future__ import annotations

from typing import Any

from server.roles.registry.stream import register_role_stream


def _text(output: Any) -> str:
    content = getattr(output, "content", output)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(parts)
    return ""


class DevicePerfStreamHooks:
    def summarize_observation(self, obs: dict) -> str:
        if not obs:
            return "(空)"
        if obs.get("error"):
            return str(obs["error"])
        return f"source={obs.get('source') or '?'}"

    def summarize_tool_result(self, name: str, output: Any) -> str | None:
        text = _text(output)
        if name == "ListDevices":
            lines = [
                line
                for line in text.splitlines()
                if line.strip() and not line.lower().startswith("no devices")
            ]
            return f"设备 {len(lines)} 台" if lines else "没有设备"
        if name == "Snapshot":
            return "已读取当前界面" if text.strip() else "没有界面"
        return None


register_role_stream("device_perf", DevicePerfStreamHooks())
