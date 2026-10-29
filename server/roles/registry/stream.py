from typing import Any, Protocol

from server.evidence.payload import unwrap_tool_payload


class RoleStreamHooks(Protocol):
    """某个扮演在流式层可插的行为。"""

    def summarize_observation(self, obs: dict) -> str: ...


_REGISTRY: dict[str, RoleStreamHooks] = {}


def register_role_stream(role: str, hooks: RoleStreamHooks) -> None:
    _REGISTRY[role] = hooks


def get_role_stream(role: str) -> RoleStreamHooks | None:
    return _REGISTRY.get(role)


def summarize_observation(role: str, obs: dict) -> str:
    hooks = get_role_stream(role)
    if hooks is not None:
        return hooks.summarize_observation(obs)
    if obs.get("error"):
        return str(obs["error"])
    return f"source={obs.get('source') or '?'}"


def summarize_tool_result(role: str, name: str, output: Any) -> str:
    """工具回包缩成一行；优先扮演钩子，否则通用截断。"""
    hooks = get_role_stream(role)
    fn = getattr(hooks, "summarize_tool_result", None) if hooks else None
    if callable(fn):
        custom = fn(name, output)
        if custom:
            return str(custom)

    content = output
    if hasattr(output, "content"):
        content = getattr(output, "content", None)

    if content is not None:
        parsed = unwrap_tool_payload(content)
        if isinstance(parsed, dict):
            if "ok" in parsed:
                return f"ok={parsed.get('ok')}"
            if "isPlaying" in parsed:
                return f"isPlaying={parsed.get('isPlaying')}"

    text = content if isinstance(content, str) else str(content)
    text = text.replace("\n", " ").strip()
    if len(text) > 100:
        text = text[:100] + "…"
    return text or "ok"

