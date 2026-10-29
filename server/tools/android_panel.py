"""真机侧栏：先扫设备给用户选，再按所选序列号拉起 Android MCP。

扫描只用 `adb devices -l`，不启动 MCP。连接时才 uvx android-mcp --device <serial>。
"""
from __future__ import annotations

import asyncio
import re
import subprocess

from server.infra.app_logging import get_logger
from server.tools.connect import (
    ANDROID_SERVER,
    disconnect_android,
    ensure_android,
    get_android_serial,
    server_status,
    tool_named,
)

_log = get_logger("ailearning.mcp")

_DEVICE_LINE = re.compile(
    r"^(?P<serial>\S+)\s+(?P<state>device|unauthorized|offline|recovery|sideload)\b(?P<rest>.*)$"
)
_MODEL = re.compile(r"\bmodel:(\S+)")
# dumpsys：窗口焦点优先，activity top 作补充。Unity 游戏没有 Android resource id，不能靠 Snapshot 猜。
_ACTIVITY = re.compile(r"\bACTIVITY\s+([A-Za-z][\w.]*)/")
_FOCUS = re.compile(r"mCurrentFocus=.*? ([A-Za-z][\w.]*)/")
_FOCUSED_APP = re.compile(r"mFocusedApp=.*? ([A-Za-z][\w.]*)/")


def clear_snapshot_cache() -> None:
    """兼容 connect 里的清理调用；侧栏前台已改走 dumpsys。"""
    return


def parse_devices_l(text: str) -> list[dict]:
    """解析 `adb devices -l`：serial / state / model。"""
    found: list[dict] = []
    for line in (text or "").splitlines():
        match = _DEVICE_LINE.match(line.strip())
        if not match:
            continue
        model_match = _MODEL.search(match.group("rest") or "")
        model = model_match.group(1).replace("_", "-") if model_match else ""
        found.append(
            {
                "serial": match.group("serial"),
                "state": match.group("state"),
                "model": model,
            }
        )
    return found


def _run_adb_devices_l() -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            ["adb", "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return 127, "", "未找到 adb"
    except subprocess.TimeoutExpired:
        return 124, "", "adb devices 超时"
    return completed.returncode, completed.stdout or "", completed.stderr or ""


async def scan_devices() -> dict:
    """给下拉框用。不启动 MCP。"""
    code, out, err = await asyncio.to_thread(_run_adb_devices_l)
    if code == 127:
        return {"ok": False, "error": err or "未找到 adb", "devices": []}
    if code != 0 and not out.strip():
        return {"ok": False, "error": (err or "adb devices 失败").strip(), "devices": []}
    return {"ok": True, "error": "", "devices": parse_devices_l(out)}


def parse_foreground_package(text: str) -> str:
    """从 dumpsys 文本取包名。"""
    for pattern in (_FOCUS, _FOCUSED_APP, _ACTIVITY):
        match = pattern.search(text or "")
        if match:
            package = match.group(1)
            if package == "android" or package.startswith("com.android.systemui"):
                continue
            return package
    return ""


def _adb_shell(serial: str, *shell_args: str, timeout: float = 8) -> str:
    try:
        completed = subprocess.run(
            ["adb", "-s", serial, "shell", *shell_args],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        _log.warning("adb shell 失败 serial=%s：%s", serial, e)
        return ""
    return completed.stdout or ""


def _run_foreground(serial: str) -> str:
    # 整段 dumpsys 拉回本机再解析，不依赖手机上的 grep（有的机没有）
    package = parse_foreground_package(_adb_shell(serial, "dumpsys", "window", timeout=12))
    if package:
        return package
    return parse_foreground_package(_adb_shell(serial, "dumpsys", "activity", "top", timeout=12))


async def read_foreground(serial: str) -> str:
    if not serial:
        return ""
    return await asyncio.to_thread(_run_foreground, serial)


async def connect_android_panel(serial: str) -> dict:
    """用户选好设备后点连接：按序列号拉起 Android MCP。"""
    serial = (serial or "").strip()
    if not serial:
        return {
            "ok": False,
            "error": "请先选择设备",
            "devices": [],
            "foreground": "",
            "serial": "",
        }
    clear_snapshot_cache()
    await ensure_android(serial)
    # MCP 起来后显式指定这台，避免它又去抢别的机
    connect_tool = tool_named(ANDROID_SERVER, "ConnectDevice")
    if connect_tool is not None:
        try:
            await connect_tool.ainvoke({"serial": serial})
        except Exception as e:
            _log.warning("ConnectDevice 失败：%s", e)
    return await android_panel()


async def disconnect_android_panel() -> dict:
    clear_snapshot_cache()
    await disconnect_android()
    return {
        "ok": False,
        "error": "",
        "devices": [],
        "foreground": "",
        "serial": "",
    }


async def android_panel() -> dict:
    """已连接时的状态：当前机 + 前台包。"""
    status = server_status(ANDROID_SERVER)
    serial = get_android_serial()
    if not status.get("ok"):
        return {
            "ok": False,
            "error": str(status.get("error") or "Android MCP 未连接"),
            "devices": [],
            "foreground": "",
            "serial": "",
        }
    scanned = await scan_devices()
    devices = [item for item in scanned.get("devices") or [] if item.get("serial") == serial]
    if not devices and serial:
        devices = [{"serial": serial, "state": "device", "model": ""}]
    foreground = ""
    if serial and any(item.get("state") == "device" for item in devices):
        try:
            foreground = await asyncio.wait_for(read_foreground(serial), timeout=10)
        except Exception as e:
            _log.warning("dumpsys 读取前台失败：%s", e)
            foreground = ""
    return {
        "ok": True,
        "error": "",
        "devices": devices,
        "foreground": foreground,
        "serial": serial,
    }
