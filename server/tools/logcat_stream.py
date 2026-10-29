"""真机 Logcat：本机 adb 推流，不经 Android MCP。

解析 `logcat -v threadtime`，按 SSE 批量推送；断连杀进程。

Windows：不用 asyncio.create_subprocess_exec（部分 uvicorn 循环会 NotImplementedError），
改用 subprocess.Popen + to_thread 读行。
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from typing import Any, AsyncIterator

from server.infra.app_logging import get_logger
from server.tools.connect import get_android_serial

_log = get_logger("ailearning.logcat")

# MM-DD HH:MM:SS.mmm  PID  TID  L TAG: message
_THREADTIME = re.compile(
    r"^(?P<date>\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\s+"
    r"(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<level>[VDIWEAF])\s+"
    r"(?P<tag>\S+)\s*:\s*(?P<message>.*)$"
)

_LEVEL_TO_PRIORITY = {
    "V": 2,
    "D": 3,
    "I": 4,
    "W": 5,
    "E": 6,
    "A": 7,
    "F": 7,
}

_PS_LINE = re.compile(r"^\S+\s+(?P<pid>\d+)\s+.*\s(?P<name>\S+)\s*$")
_BATCH_MS = 0.05
_MAX_BATCH = 80
_PS_REFRESH_S = 2.0


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def parse_threadtime_line(line: str) -> dict[str, Any] | None:
    match = _THREADTIME.match(line.rstrip("\r\n"))
    if not match:
        return None
    level = match.group("level")
    return {
        "date": match.group("date"),
        "pid": int(match.group("pid")),
        "tid": int(match.group("tid")),
        "priority": _LEVEL_TO_PRIORITY.get(level, 4),
        "tag": match.group("tag"),
        "message": match.group("message"),
        "package": "",
    }


def parse_ps_table(text: str) -> dict[int, str]:
    """从 `adb shell ps -A` 建 PID→进程名（多半是包名）。"""
    mapping: dict[int, str] = {}
    for line in (text or "").splitlines():
        match = _PS_LINE.match(line.strip())
        if not match:
            continue
        name = match.group("name")
        if name in ("NAME", "ps"):
            continue
        mapping[int(match.group("pid"))] = name
    return mapping


def _run_adb(args: list[str], *, timeout: float) -> tuple[int, bytes, bytes]:
    try:
        completed = subprocess.run(
            ["adb", *args],
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, b"", b"adb not found"
    except subprocess.TimeoutExpired:
        return 124, b"", b"adb timeout"
    return completed.returncode, completed.stdout or b"", completed.stderr or b""


async def clear_logcat(serial: str) -> dict[str, Any]:
    serial = (serial or "").strip() or get_android_serial()
    if not serial:
        return {"ok": False, "error": "未指定设备"}
    code, _out, err = await asyncio.to_thread(
        _run_adb, ["-s", serial, "logcat", "-c"], timeout=8
    )
    if code == 127:
        return {"ok": False, "error": "未找到 adb"}
    if code == 124:
        return {"ok": False, "error": "adb logcat -c 超时"}
    if code != 0:
        return {
            "ok": False,
            "error": err.decode("utf-8", "replace").strip() or "清除失败",
        }
    return {"ok": True, "error": ""}

def _ps_map_sync(serial: str) -> dict[int, str]:
    code, out, _err = _run_adb(["-s", serial, "shell", "ps", "-A"], timeout=6)
    if code != 0:
        return {}
    return parse_ps_table(out.decode("utf-8", "replace"))


async def _refresh_pid_map(serial: str) -> dict[int, str]:
    return await asyncio.to_thread(_ps_map_sync, serial)


def _spawn_logcat(serial: str) -> subprocess.Popen[bytes]:
    # CREATE_NO_WINDOW：避免 Windows 弹出黑框
    creationflags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return subprocess.Popen(
        ["adb", "-s", serial, "logcat", "-v", "threadtime", "-T", "300"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        creationflags=creationflags,
    )


async def _readline(proc: subprocess.Popen[bytes]) -> bytes:
    assert proc.stdout is not None
    return await asyncio.to_thread(proc.stdout.readline)


async def iter_logcat_sse(serial: str | None = None) -> AsyncIterator[str]:
    """SSE：status / entries / error。客户端断开时取消本协程即可杀 adb。"""
    serial = (serial or "").strip() or get_android_serial()
    if not serial:
        yield _sse({"type": "error", "content": "未指定设备，请先连接或传入 serial"})
        return

    try:
        proc = await asyncio.to_thread(_spawn_logcat, serial)
    except FileNotFoundError:
        yield _sse({"type": "error", "content": "未找到 adb"})
        return
    except Exception as e:
        yield _sse({"type": "error", "content": f"{type(e).__name__}: {e}"})
        return

    yield _sse({"type": "status", "ok": True, "serial": serial, "error": ""})

    pid_map: dict[int, str] = {}
    last_ps = 0.0
    batch: list[dict[str, Any]] = []
    last_flush = time.monotonic()
    # 堆栈续行挂到上一条，避免拆成无行 orphan
    last_entry: dict[str, Any] | None = None
    pending: asyncio.Task[bytes] | None = None

    def take_batch() -> str | None:
        nonlocal batch, last_flush
        if not batch:
            return None
        chunk = batch
        batch = []
        last_flush = time.monotonic()
        return _sse({"type": "entries", "items": chunk})

    try:
        while True:
            now = time.monotonic()
            if now - last_ps >= _PS_REFRESH_S:
                pid_map = await _refresh_pid_map(serial)
                last_ps = now

            if pending is None:
                pending = asyncio.create_task(_readline(proc))

            done, _ = await asyncio.wait({pending}, timeout=_BATCH_MS)
            if not done:
                frame = take_batch()
                if frame:
                    yield frame
                if proc.poll() is not None:
                    break
                continue

            raw = pending.result()
            pending = None
            if not raw:
                break
            line = raw.decode("utf-8", "replace")
            entry = parse_threadtime_line(line)
            if entry is None:
                text = line.rstrip("\r\n")
                if last_entry is not None and text:
                    last_entry["message"] = f"{last_entry['message']}\n{text}"
                continue
            entry["package"] = pid_map.get(entry["pid"]) or ""
            last_entry = entry
            batch.append(entry)
            if len(batch) >= _MAX_BATCH or time.monotonic() - last_flush >= _BATCH_MS:
                frame = take_batch()
                if frame:
                    yield frame

        frame = take_batch()
        if frame:
            yield frame
        err = b""
        if proc.stderr is not None:
            try:
                err = await asyncio.wait_for(
                    asyncio.to_thread(proc.stderr.read), timeout=0.2
                )
            except (asyncio.TimeoutError, Exception):
                err = b""
        code = proc.poll()
        if code not in (0, None):
            msg = err.decode("utf-8", "replace").strip() or f"logcat 退出码 {code}"
            yield _sse({"type": "error", "content": msg})
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _log.warning("logcat 推流异常：%s", e)
        yield _sse({"type": "error", "content": f"{type(e).__name__}: {e}"})
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except (asyncio.CancelledError, Exception):
                pass
        if proc.poll() is None:
            proc.kill()
            try:
                await asyncio.to_thread(proc.wait)
            except Exception:
                pass
