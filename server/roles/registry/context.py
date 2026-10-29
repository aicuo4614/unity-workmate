"""扮演本局上下文：注入 analyze/greet 的软事实，不改图骨架。"""
from __future__ import annotations

from typing import Protocol


class RoleContextHooks(Protocol):
    async def session_brief(self) -> str: ...


_REGISTRY: dict[str, RoleContextHooks] = {}


def register_role_context(role: str, hooks: RoleContextHooks) -> None:
    _REGISTRY[role] = hooks


async def build_session_context(role: str) -> str:
    """无登记或空串时，调用方直接跳过。"""
    hooks = _REGISTRY.get(role)
    if hooks is None:
        return ""
    try:
        return (await hooks.session_brief() or "").strip()
    except Exception:
        return ""
