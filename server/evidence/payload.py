"""MCP / ToolMessage 回包解包（通用，不绑扮演）。"""
from __future__ import annotations

import json
from typing import Any


def unwrap_tool_payload(raw: Any) -> Any:
    """ToolMessage.content 可能是 str，也可能是 [{type,text}] 内容块。"""
    if raw is None:
        return None

    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return raw

    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and "text" in first:
            text = first.get("text")
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        return data

    if isinstance(data, dict) and data.get("type") == "text" and "text" in data:
        text = data.get("text")
        if isinstance(text, str):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text

    return data
