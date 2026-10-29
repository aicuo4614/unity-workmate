"""口播正文清理：剥掉 / 解析模型误写入 content 的伪工具调用标记。"""
from __future__ import annotations

import re
import uuid
from typing import Any

# 全角竖线 / 尖括号先归一，再剥伪工具块。
_NORMALIZE = str.maketrans({"｜": "|", "＜": "<", "＞": ">"})

# 伪工具起点：带或不带 || / DSML / 尖括号；吃到行末，保留行首中文。
_FAKE_TOOL_TAIL = re.compile(
    r"(?is)"
    r"(?:"
    r"(?:\|{1,2}(?:DSML\|?)?)?(?:calls|invoke|parameter|function)\s*>"
    r"|"
    r"(?:\|{1,2}(?:DSML\|?)?)?(?:invokename|parametername)\s*="
    r"|"
    r"invoke\s*name\s*="
    r"|"
    r"parameter\s*name\s*="
    r"|"
    r"<[^>\n]*(?:DSML|invoke|parameter|function_?call)[^>\n]*>"
    r"|"
    r"(?<![A-Za-z\u4e00-\u9fff])invokename\s*=\s*\"[^\"]+\""
    r").*$"
)

_PIPE_TAG_FRAG = re.compile(
    r"(?is)"
    r"(?:"
    r"\|{1,2}(?:DSML\|?)?[a-z_][\w]*\s*(?:=\"[^\"]*\"|[=>][^|]*)?"
    r"|"
    r"\b(?:invokename|parametername)\s*=\s*\"[^\"]*\""
    r"|"
    r"\bstring\s*=\s*\"[^\"]*\""
    r"|"
    r"\b(?:calls|invoke|parameter|function)\s*>"
    r")"
)

_MARKUP_NEEDLES = (
    "dsml",
    "invokename=",
    "parametername=",
    "invoke name=",
    "parameter name=",
    "||calls",
    "|calls>",
    "calls>",
    "||invoke",
    "||parameter",
    'string="false"',
    "string='false'",
)

# invokename="unity_foo" 或 invoke name="unity_foo"
_INVOKE_NAME = re.compile(
    r"(?is)(?:\|{0,2}(?:DSML\|?)?)?(?:invoke\s*name|invokename)\s*=\s*[\"']([^\"']+)[\"']"
)

# parametername="index"string="false">75parameter>
# 也兼容 ||parametername="limit"string="false">24||parameter>
_PARAM = re.compile(
    r"(?is)(?:\|{0,2}(?:DSML\|?)?)?(?:parameter\s*name|parametername)\s*=\s*[\"']([^\"']+)[\"']"
    r"(?:\s*string\s*=\s*[\"'][^\"']*[\"'])?"
    r"\s*>\s*([^<|>]*?)\s*"
    r"(?:\|{0,2})?parameter\s*>"
)


def has_tool_markup(text: str) -> bool:
    """原文是否疑似把工具调用写成了正文。"""
    if not text:
        return False
    t = text.translate(_NORMALIZE)
    low = t.lower()
    return any(n in low for n in _MARKUP_NEEDLES)


def strip_tool_markup(text: str) -> str:
    if not text:
        return ""
    t = text.translate(_NORMALIZE)
    out_lines: list[str] = []
    for line in t.splitlines():
        cut = _FAKE_TOOL_TAIL.sub("", line)
        prev = None
        while prev != cut:
            prev = cut
            cut = _PIPE_TAG_FRAG.sub("", cut)
        s = cut.strip()
        if not s:
            if line.strip():
                continue
            out_lines.append("")
            continue
        if has_tool_markup(s):
            continue
        if s.startswith("<") or s.startswith("|"):
            continue
        out_lines.append(cut.rstrip())
    while out_lines and not out_lines[-1].strip():
        out_lines.pop()
    return "\n".join(out_lines).strip()


def _coerce_arg(raw: str) -> Any:
    s = (raw or "").strip()
    if not s:
        return s
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        if re.fullmatch(r"-?\d+\.\d+", s):
            return float(s)
    except ValueError:
        pass
    return s


def _resolve_tool_name(name: str, allowed: set[str] | None) -> str | None:
    n = (name or "").strip()
    if not n:
        return None
    if not allowed:
        return n
    if n in allowed:
        return n
    if f"unity_{n}" in allowed:
        return f"unity_{n}"
    # 短名：frame_debugger_get_event → unity_frame_debugger_get_event
    for a in allowed:
        if a == n or a.endswith(n) or a.endswith(n.removeprefix("unity_")):
            return a
    return None


def parse_fake_tool_calls(
    text: str,
    *,
    allowed_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """从正文伪标记解析出 LangChain tool_calls 列表；解析失败返回 []。"""
    if not text or not has_tool_markup(text):
        return []
    t = text.translate(_NORMALIZE)
    calls: list[dict[str, Any]] = []
    matches = list(_INVOKE_NAME.finditer(t))
    for i, m in enumerate(matches):
        raw_name = m.group(1)
        name = _resolve_tool_name(raw_name, allowed_names)
        if not name:
            print(f"[spoken_text] 伪调用工具名无法解析/不在白名单: {raw_name!r}")
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(t)
        block = t[m.end() : end]
        args: dict[str, Any] = {}
        for pm in _PARAM.finditer(block):
            key = (pm.group(1) or "").strip()
            val = _coerce_arg(pm.group(2) or "")
            if key:
                args[key] = val
        calls.append(
            {
                "name": name,
                "args": args,
                "id": f"fake_{uuid.uuid4().hex[:10]}",
                "type": "tool_call",
            }
        )
    return calls
