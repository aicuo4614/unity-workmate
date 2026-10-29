"""按 mcp.json 逐台连接 MCP。一台失败不拆其他服务器。

多台塞进同一个 Client 时，一台失败会拆掉其余已连上的，工具名还会被加前缀。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from langchain.mcp import MCPAdapter

from server.infra.app_logging import get_logger

_log = get_logger("ailearning.mcp")

UNITY_SERVER = "kabu-unity-mcp"
ANDROID_SERVER = "android-mcp"

# 就地改这份列表。reconnect 若换成新 list，已经 import 走的引用看不到新工具。
tools: list = []
_adapters: dict[str, MCPAdapter] = {}
_server_tools: dict[str, list] = {}
_server_status: dict[str, dict] = {}
# 侧栏选中并连上的那台；断开后清空
_android_serial: str = ""

# 给 /health、连接页看的是 Unity 这一台。Android 是否在线不改这个字段。
mcp_status: dict = {
    "ok": False,
    "url": "",
    "tool_count": 0,
    "error": "尚未初始化",
}


def _config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "mcp" / "mcp.json"


def _load_config() -> dict:
    with _config_path().open(encoding="utf-8") as f:
        return json.load(f)


def _server_target(cfg: dict) -> str:
    if cfg.get("url"):
        return str(cfg["url"])
    command = str(cfg.get("command") or "")
    args = " ".join(str(part) for part in (cfg.get("args") or []))
    return f"{command} {args}".strip()


def _set_tools(listed: list) -> None:
    tools.clear()
    tools.extend(listed)


def tools_of(server: str) -> list:
    return list(_server_tools.get(server, []))


def server_status(name: str) -> dict:
    return dict(_server_status.get(name) or {})


def tool_named(server: str, name: str):
    for tool in _server_tools.get(server, []):
        if getattr(tool, "name", None) == name:
            return tool
    return None


def _publish_unity_status() -> None:
    st = _server_status.get(UNITY_SERVER) or {}
    mcp_status["ok"] = bool(st.get("ok"))
    mcp_status["url"] = str(st.get("target") or "")
    mcp_status["tool_count"] = int(st.get("tool_count") or 0)
    mcp_status["error"] = str(st.get("error") or "")
    if UNITY_SERVER not in _server_status and not mcp_status["error"]:
        mcp_status["error"] = "尚未初始化"


async def _close_adapter(name: str) -> None:
    adapter = _adapters.pop(name, None)
    _server_tools.pop(name, None)
    if adapter is None:
        return
    try:
        await adapter.__aexit__(None, None, None)
    except Exception:
        pass


# Unity 未开时别把 lifespan 拖死；超时后 API（含 adb 扫描）仍可对外服务
_MCP_CONNECT_TIMEOUT_SEC = 8.0


async def _connect_one(name: str, cfg: dict) -> None:
    """连一台。失败只记在这台的状态上，不拆其他服务器。"""
    await _close_adapter(name)
    status = {
        "ok": False,
        "target": _server_target(cfg),
        "tool_count": 0,
        "error": "",
    }
    adapter: MCPAdapter | None = None
    try:
        adapter = MCPAdapter({"mcpServers": {name: cfg}})
        await asyncio.wait_for(adapter.__aenter__(), timeout=_MCP_CONNECT_TIMEOUT_SEC)
        listed = await asyncio.wait_for(
            adapter.list_tools(),
            timeout=_MCP_CONNECT_TIMEOUT_SEC,
        )
        _adapters[name] = adapter
        _server_tools[name] = listed
        status["tool_count"] = len(listed)
        status["ok"] = bool(listed)
        if not listed:
            status["error"] = "已连接但工具列表为空"
        _log.info("MCP %s 已连接 tools=%s", name, len(listed))
    except Exception as e:
        if adapter is not None and name not in _adapters:
            try:
                await adapter.__aexit__(None, None, None)
            except Exception:
                pass
        status["error"] = f"{type(e).__name__}: {e}"
        _log.warning(
            "MCP %s 连接失败 target=%s 原因=%s",
            name,
            status["target"],
            status["error"],
        )
    _server_status[name] = status


async def connect_configured_servers(*, skip: set[str] | None = None) -> list:
    """按 mcp.json 逐台连接。skip 里的服务器保持原样，启动时用来放过 android-mcp。"""
    skipped = skip or set()
    config = _load_config()
    servers = config.get("mcpServers") or {}
    wanted = {
        name: cfg
        for name, cfg in servers.items()
        if isinstance(cfg, dict) and (cfg.get("url") or cfg.get("command"))
    }
    for name in list(_adapters):
        if name in skipped:
            continue
        if name not in wanted:
            await _close_adapter(name)
            _server_status.pop(name, None)
    pending = {name: cfg for name, cfg in wanted.items() if name not in skipped}
    if pending:
        await asyncio.gather(*(_connect_one(name, cfg) for name, cfg in pending.items()))
    _publish_unity_status()
    flat: list = []
    for listed in _server_tools.values():
        flat.extend(listed)
    _set_tools(flat)
    return tools


def _rebuild_tools() -> None:
    flat: list = []
    for listed in _server_tools.values():
        flat.extend(listed)
    _set_tools(flat)


def get_android_serial() -> str:
    return _android_serial


async def ensure_android(serial: str) -> None:
    """按用户选的序列号拉起 android-mcp。换机或未连上时才会重启进程。"""
    global _android_serial
    serial = (serial or "").strip()
    if not serial:
        _server_status[ANDROID_SERVER] = {
            "ok": False,
            "target": "",
            "tool_count": 0,
            "error": "请先选择设备",
        }
        return
    current = _server_status.get(ANDROID_SERVER) or {}
    if current.get("ok") and _android_serial == serial:
        return
    config = _load_config()
    base = (config.get("mcpServers") or {}).get(ANDROID_SERVER)
    if not isinstance(base, dict):
        _server_status[ANDROID_SERVER] = {
            "ok": False,
            "target": "",
            "tool_count": 0,
            "error": "mcp.json 没有 android-mcp",
        }
        return
    # 拷一份再加 --device，不改 mcp.json 落盘内容
    cfg = dict(base)
    args = [str(part) for part in (cfg.get("args") or [])]
    args.extend(["--device", serial])
    cfg["args"] = args
    await _connect_one(ANDROID_SERVER, cfg)
    if (_server_status.get(ANDROID_SERVER) or {}).get("ok"):
        _android_serial = serial
    else:
        _android_serial = ""
    _rebuild_tools()


async def disconnect_android() -> None:
    """侧栏点断开：只关 android-mcp，不动 Unity。"""
    global _android_serial
    await _close_adapter(ANDROID_SERVER)
    _server_status.pop(ANDROID_SERVER, None)
    _android_serial = ""
    _rebuild_tools()
    _log.info("Android MCP 已断开")


async def init_tools() -> list:
    """启动时调用。android-mcp 不在这里连，等侧栏按钮。"""
    listed = await connect_configured_servers(skip={ANDROID_SERVER})
    if not mcp_status["ok"]:
        _log.warning("Unity MCP 未连接 — 场景分析暂无编辑器工具")
        _log.warning(
            "MCP 地址=%s 原因=%s",
            mcp_status.get("url") or "(未知)",
            mcp_status.get("error") or "尚未初始化",
        )
        _log.warning("请先启动 Unity kabu-unity-mcp（默认 3828）")
    elif "unity_perf_capture_frame" not in {getattr(t, "name", "") for t in listed}:
        _log.warning("MCP 缺少 unity_perf_capture_frame")
    return listed


async def reconnect_mcp(url: str | None = None, *, persist: bool = True) -> dict:
    """热重连 Unity MCP。不重启 android-mcp，避免改编辑器地址时拆掉真机连接。"""
    path = _config_path()
    config = _load_config()
    if url:
        url = url.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("MCP URL 必须以 http:// 或 https:// 开头")
        servers = config.setdefault("mcpServers", {})
        current = servers.get(UNITY_SERVER)
        if not isinstance(current, dict):
            current = {}
            servers[UNITY_SERVER] = current
        current["url"] = url
        if persist:
            path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    servers = config.get("mcpServers") or {}
    cfg = servers.get(UNITY_SERVER)
    if not isinstance(cfg, dict):
        mcp_status["ok"] = False
        mcp_status["url"] = ""
        mcp_status["tool_count"] = 0
        mcp_status["error"] = "mcp.json 没有 kabu-unity-mcp"
        return dict(mcp_status)
    await _connect_one(UNITY_SERVER, cfg)
    _publish_unity_status()
    flat: list = []
    for listed in _server_tools.values():
        flat.extend(listed)
    _set_tools(flat)
    return dict(mcp_status)


async def close_tools():
    global _android_serial
    for name in list(_adapters):
        await _close_adapter(name)
    _server_status.clear()
    _android_serial = ""
    _set_tools([])
    mcp_status["ok"] = False
    mcp_status["tool_count"] = 0
    mcp_status["url"] = ""
    mcp_status["error"] = "已关闭"
    # 侧栏缓存留在 android_panel，这里晚导入，避免连接模块去依赖侧栏解析
    from server.tools.android_panel import clear_snapshot_cache

    clear_snapshot_cache()
