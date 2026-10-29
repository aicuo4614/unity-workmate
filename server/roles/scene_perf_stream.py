"""帧诊扮演：流式观测摘要与工具回包一行摘要。"""
from __future__ import annotations

from typing import Any

from server.evidence.payload import unwrap_tool_payload
from server.roles.registry.stream import register_role_stream


class ScenePerfStreamHooks:
    def summarize_observation(self, obs: dict) -> str:
        if not obs:
            return "(空)"
        if obs.get("error"):
            return str(obs["error"])

        parsed = unwrap_tool_payload(obs.get("raw"))
        if isinstance(parsed, dict):
            basics = parsed.get("basics") or {}
            data = basics.get("data") or {}
            if "batches" in data:
                return f"Batches={data.get('batches')}  basics.ok={basics.get('ok')}"
            if "ok" in parsed:
                return f"ok={parsed.get('ok')}"

        if isinstance(parsed, str):
            return parsed[:120]
        return f"source={obs.get('source') or '?'}"

    def summarize_tool_result(self, name: str, output: Any) -> str | None:
        content = output
        if hasattr(output, "content"):
            content = getattr(output, "content", None)
        if content is None:
            return None

        parsed = unwrap_tool_payload(content)
        if isinstance(parsed, dict):
            basics = parsed.get("basics") or {}
            data = basics.get("data") or {}
            if "batches" in data:
                return f"Batches={data.get('batches')}  ok={basics.get('ok')}"
            if "ok" in parsed:
                return f"ok={parsed.get('ok')}"
            if "isPlaying" in parsed:
                return f"isPlaying={parsed.get('isPlaying')}"
        return None


register_role_stream("scene_perf", ScenePerfStreamHooks())
