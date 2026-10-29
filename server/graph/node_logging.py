"""节点切换日志：上一跳 → 当前节点。"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from server.infra.app_logging import get_logger

_log = get_logger("ailearning.graph")

# 模块级变量：LangGraph 各节点常在不同 context/task 里跑，
# ContextVar 的 set 带不到下一站，会一直打成 START → xxx。
_last_node: str = "START"


def reset_route(start: str | None = "START") -> None:
    global _last_node
    _last_node = start or "START"


def log_transition(to_node: str, *, kind: str = "node") -> None:
    global _last_node
    _log.info("%s → %s  (%s)", _last_node, to_node, kind)
    _last_node = to_node


def with_node_log(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    """包装节点函数，进入时打印上一节点 → name。"""

    if asyncio.iscoroutinefunction(fn):

        async def _async(state, *args, **kwargs):
            log_transition(name, kind="node")
            return await fn(state, *args, **kwargs)

        _async.__name__ = getattr(fn, "__name__", name)
        return _async

    # ToolNode 等：不要 fn(state)，走 ainvoke（保持在同一异步上下文）
    if hasattr(fn, "ainvoke") and callable(getattr(fn, "ainvoke")):

        async def _via_ainvoke(state, *args, **kwargs):
            log_transition(name, kind="node")
            return await fn.ainvoke(state, *args, **kwargs)

        _via_ainvoke.__name__ = name
        return _via_ainvoke

    def _sync(state, *args, **kwargs):
        log_transition(name, kind="node")
        return fn(state, *args, **kwargs)

    _sync.__name__ = getattr(fn, "__name__", name)
    return _sync


def with_route_log(route_fn: Callable[..., Any]) -> Callable[..., Any]:
    """包装 tools_condition 一类路由：打印将去的分支。"""

    def _wrapped(state, *args, **kwargs):
        dest = route_fn(state, *args, **kwargs)
        label = "END" if dest in ("__end__", END_SENTINEL) else str(dest)
        # 路由只预告下一跳，真正进节点时再由 with_node_log 打一遍也可以；
        # 这里用 route 方便看到 agent→tools / agent→END
        _log.info("route: %s ⇒ %s", _last_node, label)
        return dest

    return _wrapped


# tools_condition 返回 "__end__"
END_SENTINEL = "__end__"
