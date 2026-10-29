from typing import Any, Protocol

from server import state
from server.roles.defaults import DEFAULT_ROLE


class RoleReportBuilder(Protocol):
    def build_report(self, agent_state: state.AgentState) -> dict[str, Any]:
        ...


_REGISTRY: dict[str, RoleReportBuilder] = {}


def register_role_report(role: str, builder: RoleReportBuilder) -> None:
    _REGISTRY[role] = builder


def get_role_report(role: str) -> RoleReportBuilder | None:
    return _REGISTRY.get(role)


def build_report(role: str, agent_state: state.AgentState) -> dict[str, Any]:
    builder = get_role_report(role)
    if builder is None:
        return {
            "summary": f"扮演 {role} 尚未注册 report 构建器",
            "evidence": [],
            "suspects": [],
            "conflicts": [],
        }
    return builder.build_report(agent_state)


_ACTION_LABEL = {
    "approve": "批准执行",
    "skip": "跳过",
    "advise_only": "只要建议（未改场景）",
}


def build_closing_facts(role: str, agent_state: state.AgentState) -> dict[str, Any]:
    """汇报站事实骨架。

    优先调用扮演 builder.build_closing_facts；未实现时用通用 HITL/报告/建议字段，
    避免通用层依赖某一扮演的 Batches 解析。
    """
    builder = get_role_report(role)
    fn = getattr(builder, "build_closing_facts", None) if builder else None
    if callable(fn):
        return fn(agent_state)

    report = agent_state.get("report") or {}
    plan = agent_state.get("plan") or {}
    approval = agent_state.get("approval") or {}
    action = str(approval.get("action") or "")
    last_obs = agent_state.get("last_observation") or {}
    suspects = report.get("suspects") or []
    steps = plan.get("steps") or []
    return {
        "hitl_action": action,
        "hitl_label": _ACTION_LABEL.get(action, action or "未记录"),
        "diagnose_summary": report.get("summary") or "",
        "suspect_count": len(suspects) if isinstance(suspects, list) else 0,
        "advise_summary": plan.get("summary") or "",
        "step_count": len(steps) if isinstance(steps, list) else 0,
        "step_index": int(agent_state.get("step_index") or 0),
        "executed": action == "approve",
        "modified": bool(last_obs.get("act_wrote")),
        "recaptured": bool(last_obs.get("act_captured")),
        "role": role or DEFAULT_ROLE,
    }


def closing_polish_hint(role: str) -> str:
    builder = get_role_report(role)
    fn = getattr(builder, "closing_polish_hint", None) if builder else None
    if callable(fn):
        return str(fn())
    return (
        "你在「汇报」阶段。下面有一份事实骨架 JSON。"
        "请只输出简体中文汇报（不要 markdown/JSON），以「【汇报】」开头；"
        "沿用骨架数字与布尔，禁止编造成功结论；不要工具、不要反问。"
    )


def fallback_closing_spoken(role: str, facts: dict[str, Any]) -> str:
    builder = get_role_report(role)
    fn = getattr(builder, "fallback_closing_spoken", None) if builder else None
    if callable(fn):
        return str(fn(facts))
    lines = [
        f"【汇报】本局 HITL：{facts.get('hitl_label')}。",
        f"诊断：{facts.get('diagnose_summary') or '（无）'}",
        f"建议：{facts.get('advise_summary') or '（无）'}",
    ]
    if facts.get("executed"):
        lines.append(
            "已批准执行；"
            + ("有写入。" if facts.get("modified") else "未见写入。")
            + ("已复采。" if facts.get("recaptured") else "未见复采。")
        )
    elif facts.get("hitl_action") in ("advise_only", "skip"):
        lines.append("未进入执行。")
    return "\n".join(lines)
