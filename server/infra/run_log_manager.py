"""开发态对话时间线：server/logs/sessions/*.md + latest.md。

不写「一工具一 JSON」。prod（app_env=prod）整段 no-op。
业务只调 begin_run / 各 log_*；完整 MCP 回包进 evidence，不进本目录。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from common.config import is_dev
from server.infra.app_logging import get_logger, log_dir

_sessions_dir: Path | None = None
# thread_id → 本局 md 路径（同 thread 的 resume 追加，不新开文件）
_active: dict[str, Path] = {}
_token_buf: dict[str, str] = {}
_current_thread: str | None = None


def _safe(name: str) -> str:
    s = re.sub(r"[^\w\-.]+", "_", name or "default")
    return s[:80] or "default"


def _sessions_root() -> Path:
    global _sessions_dir
    if _sessions_dir is None:
        _sessions_dir = log_dir() / "sessions"
        _sessions_dir.mkdir(parents=True, exist_ok=True)
    return _sessions_dir


def _path_for(thread_id: str) -> Path | None:
    return _active.get(thread_id)


def _append(thread_id: str, text: str) -> None:
    path = _path_for(thread_id)
    if path is None:
        return
    with path.open("a", encoding="utf-8") as f:
        f.write(text)
    _sync_latest(path)


def _sync_latest(path: Path) -> None:
    latest = log_dir() / "latest.md"
    latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


def _flush_tokens(thread_id: str) -> None:
    buf = _token_buf.pop(thread_id, "")
    if not buf.strip():
        return
    _append(thread_id, f"\n{buf.rstrip()}\n")


def begin_run(thread_id: str, user_text: str = "", *, kind: str = "start") -> Path | None:
    """开一轮流（首聊或 resume）。dev 才落盘。"""
    global _current_thread
    if not is_dev():
        return None

    tid = thread_id or "default"
    _current_thread = tid
    _flush_tokens(tid)

    existing = _active.get(tid)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if existing is None or not existing.is_file():
        path = _sessions_root() / f"{stamp}_{_safe(tid)}.md"
        path.write_text(
            f"# session {tid}\n"
            f"- started: {datetime.now().isoformat(timespec='seconds')}\n"
            f"- kind: {kind}\n\n",
            encoding="utf-8",
        )
        _active[tid] = path
        get_logger().info("session log → %s", path)
    else:
        path = existing
        _append(tid, f"\n---\n## turn {stamp} ({kind})\n\n")

    if user_text.strip():
        _append(tid, f"**user**: {user_text.strip()}\n\n")
    _sync_latest(path)
    return path


def log_phase(name: str, title: str | None = None) -> None:
    if not is_dev() or not _current_thread:
        return
    tid = _current_thread
    _flush_tokens(tid)
    label = title or name
    _append(tid, f"\n### {label} (`{name}`)\n")


def log_token(content: str) -> None:
    if not is_dev() or not _current_thread or not content:
        return
    tid = _current_thread
    _token_buf[tid] = _token_buf.get(tid, "") + content


def log_tool(name: str, *, args=None, summary: str | None = None) -> None:
    if not is_dev() or not _current_thread:
        return
    tid = _current_thread
    _flush_tokens(tid)
    bits = [f"- tool `{name}`"]
    if summary:
        bits.append(summary)
    elif isinstance(args, dict) and args:
        try:
            bits.append(json.dumps(args, ensure_ascii=False)[:120])
        except TypeError:
            pass
    _append(tid, " ".join(bits) + "\n")


def log_gate(kind: str, detail: str = "") -> None:
    if not is_dev() or not _current_thread:
        return
    tid = _current_thread
    _flush_tokens(tid)
    extra = f" {detail}" if detail else ""
    _append(tid, f"\n**{kind}**{extra}\n")


def log_error(content: str) -> None:
    if not is_dev() or not _current_thread:
        return
    tid = _current_thread
    _flush_tokens(tid)
    _append(tid, f"\n**error**: {content}\n")


def end_run() -> None:
    if not is_dev() or not _current_thread:
        return
    tid = _current_thread
    _flush_tokens(tid)
    _append(tid, "\n*(run_end)*\n")


# —— 兼容旧调用名（run_tools / tool_run_log）——

def dump_tool(name: str, content, args=None) -> None:
    """兼容旧入口：时间线工具行由 stream_bridge 写，避免与 TOOL_RESULT 重复。"""
    return
