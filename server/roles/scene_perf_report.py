"""scene_perf：分析用事实骨架（写入 report 槽位供汇总）。"""
from __future__ import annotations

from typing import Any

from server import state
from server.evidence.pack import (
    capture_payload,
    missing_kinds,
    report_evidence_list,
)
from server.evidence.payload import unwrap_tool_payload
from server.roles.registry.report import register_role_report


def _batches_from_capture(capture: dict) -> int | None:
    data = (capture.get("basics") or {}).get("data") or {}
    v = data.get("batches")
    return int(v) if isinstance(v, (int, float)) else None


class ScenePerfReportBuilder:
    def build_report(self, agent_state: state.AgentState) -> dict[str, Any]:
        evidence = agent_state.get("evidence") or {}
        last_obs = agent_state.get("last_observation") or {}
        cur_cap = capture_payload(evidence)
        if not cur_cap:
            parsed = unwrap_tool_payload(last_obs.get("raw"))
            cur_cap = parsed if isinstance(parsed, dict) else {}

        current_value = _batches_from_capture(cur_cap)
        ctx = cur_cap.get("context") or {}
        gaps = missing_kinds(evidence)
        if current_value is None:
            summary = "尚未拿到有效 Batches，请结合 gaps 与其它证据。"
        elif gaps:
            summary = (
                f"当前 Batches={current_value}；证据仍缺 {', '.join(gaps)}。"
            )
        else:
            summary = f"当前 Batches={current_value}；可结合 sweep/overdraw 等继续追问。"

        return {
            "metric_primary": "batches",
            "current_value": current_value,
            "scene": str(ctx.get("scenePath") or ""),
            "play_mode": str(ctx.get("playMode") or ""),
            "evidence": report_evidence_list(evidence),
            "evidence_missing": gaps,
            "summary": summary,
        }

    def build_closing_facts(self, agent_state: state.AgentState) -> dict[str, Any]:
        analyze = agent_state.get("analyze") or {}
        return {
            "analyze_summary": str(analyze.get("summary") or "")[:800],
            "last_answer": str(analyze.get("last_answer") or "")[:400],
        }

    def closing_polish_hint(self) -> str:
        return "用简体中文简短收束即可。"

    def fallback_closing_spoken(self, facts: dict[str, Any]) -> str:
        return "【分析】" + str(facts.get("analyze_summary") or "本局暂无汇总。")


register_role_report("scene_perf", ScenePerfReportBuilder())
