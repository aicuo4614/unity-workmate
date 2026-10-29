"""管理本机模型配置与每轮模型快照；不编译图、不存储会话。"""
from contextvars import ContextVar
from functools import wraps
from inspect import isasyncgenfunction
from pathlib import Path
import json
import os
import tempfile

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from common.config import settings

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "model_config.json"
_current_model = None
_run_model: ContextVar[ChatOpenAI | None] = ContextVar("run_model", default=None)


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr | None = None
    base_url: str = "https://api.deepseek.com"
    model: str = Field(default="deepseek-chat", min_length=1, max_length=128)
    request_timeout: int = Field(default=120, ge=5, le=600)

    @field_validator("base_url")
    @classmethod
    def deepseek_url(cls, value):
        # 首版仅接受官方地址，避免将已保存的 Key 发给任意地址。
        value = value.strip().rstrip("/")
        if value not in ("https://api.deepseek.com", "https://api.deepseek.com/v1"):
            raise ValueError("请使用 DeepSeek 官方接口地址")
        return value

    @field_validator("model")
    @classmethod
    def trim_model(cls, value):
        if not value.strip():
            raise ValueError("模型名称不能为空")
        return value.strip()


_config: ModelConfig | None = None


def _load():
    global _config
    if _config is None:
        if _CONFIG_PATH.exists():
            _config = ModelConfig.model_validate_json(_CONFIG_PATH.read_text(encoding="utf-8"))
        else:
            # 兼容原有 .env；保存后的本机配置（包括清空）优先。
            _config = ModelConfig(api_key=settings.api_key, base_url=settings.base_url,
                                  model=settings.model, request_timeout=settings.request_timeout)
    return _config


def public_config():
    c = _load()
    return {"provider": "deepseek", "configured": bool(_key(c)),
            "base_url": c.base_url, "model": c.model, "request_timeout": c.request_timeout}


def _key(c):
    return c.api_key.get_secret_value().strip() if c.api_key else ""


def _resolve(update):
    c = update.model_copy(deep=True)
    if c.api_key is None:
        c.api_key = _load().api_key
    return c


def _build(c):
    if not _key(c):
        raise ValueError("请先在大模型配置中填写 API Key")
    return ChatOpenAI(model=c.model, base_url=c.base_url, api_key=_key(c),
                      timeout=c.request_timeout, max_retries=0)


def save_config(update):
    global _config, _current_model
    c = _resolve(update)
    model = _build(c) if _key(c) else None
    payload = c.model_dump(exclude={"api_key"}) | {"api_key": _key(c)}
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 原子替换，避免进程中断留下半份配置；临时文件不包含在日志中。
    fd, tmp = tempfile.mkstemp(dir=_CONFIG_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, _CONFIG_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    _config, _current_model = c, model
    return public_config()


async def test_config(update):
    model = _build(_resolve(update))
    await model.ainvoke("Reply OK", max_tokens=4)


def get_model():
    global _current_model
    pinned = _run_model.get()
    if pinned is not None:
        return pinned
    if _current_model is None:
        _current_model = _build(_load())
    return _current_model


def with_model_snapshot(fn):
    """同一轮的所有节点使用同一实例；配置更新只影响后续轮次。"""
    if isasyncgenfunction(fn):
        @wraps(fn)
        async def stream(*args, **kwargs):
            token = _run_model.set(get_model())
            try:
                async for item in fn(*args, **kwargs):
                    yield item
            finally:
                _run_model.reset(token)
        return stream
    @wraps(fn)
    async def invoke(*args, **kwargs):
        token = _run_model.set(get_model())
        try:
            return await fn(*args, **kwargs)
        finally:
            _run_model.reset(token)
    return invoke
