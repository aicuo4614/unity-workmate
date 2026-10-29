from enum import StrEnum
from typing import Any

import pydantic


class StreamEventType(StrEnum):
    """流式通道上的应用事件类型（自定义，不是 SSE 内置）。"""

    RUN_START = "run_start"
    PHASE = "phase"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    OBSERVATION = "observation"
    TOKEN = "token"
    RUN_END = "run_end"
    ERROR = "error"
    NEED_APPROVAL = "need_approval"
    NEED_CLARIFY = "need_clarify"
    # 单次 LLM 调用用量；客户端按会话累加
    USAGE = "usage"


class StreamEvent(pydantic.BaseModel):
    """一条推给 client 的事件。按 type 选用字段，其余可空。"""

    type: StreamEventType

    # token / error
    content: str | None = None

    # phase / tool_*
    name: str | None = None
    title: str | None = None
    args: dict[str, Any] | None = None
    summary: str | None = None

    # observation
    source: str | None = None

    # run_start
    role: str | None = None
    thread_id: str | None = None

    # need_approval
    plan_summary: str | None = None
    steps: list[dict[str, Any]] | None = None

    # need_clarify：gate=hitl（等人输入）
    gate: str | None = None

    # usage（单次 LLM；字段名对齐 ChatResponse）
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    def to_sse_data(self) -> dict:
        """序列化成 SSE data 用的 dict，去掉 None。"""
        return self.model_dump(exclude_none=True)