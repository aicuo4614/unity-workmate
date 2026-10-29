"""device_perf：分析骨架。这一阶段没有帧时间采样。"""
from __future__ import annotations

from typing import Any

from server import state
from server.roles.registry.report import register_role_report


class DevicePerfReportBuilder:
    def build_report(self, agent_state: state.AgentState) -> dict[str, Any]:
        del agent_state
        return {
            "summary": "还没有帧时间采样。",
            "evidence": [],
            "evidence_missing": ["frame_time"],
        }


register_role_report("device_perf", DevicePerfReportBuilder())
