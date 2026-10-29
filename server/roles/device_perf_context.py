"""device_perf：把侧栏已连设备与 dumpsys 前台包装进对话上下文。

侧栏连接与图跑次解耦；分析站不调 Snapshot 猜前台（Unity 常无 ResourceId）。
"""
from __future__ import annotations

from server.roles.registry.context import register_role_context
from server.tools.android_panel import android_panel


class DevicePerfContext:
    async def session_brief(self) -> str:
        panel = await android_panel()
        if not panel.get("ok"):
            err = str(panel.get("error") or "").strip()
            return (
                "本局真机上下文：Android MCP 未连接。"
                + (f"（{err}）" if err else "")
                + "提醒用户先在左侧选机连接；禁止编造序列号或包名。"
            )
        serial = str(panel.get("serial") or "").strip()
        foreground = str(panel.get("foreground") or "").strip()
        devices = panel.get("devices") or []
        model = ""
        for item in devices:
            if isinstance(item, dict) and item.get("serial") == serial:
                model = str(item.get("model") or "").strip()
                break
        lines = ["本局真机上下文（侧栏状态，优先于 Snapshot 猜前台）："]
        if serial:
            lines.append(f"- 已连接序列号：{serial}" + (f"（{model}）" if model else ""))
        if foreground:
            lines.append(f"- 当前前台包名：{foreground}")
            lines.append("默认围绕该包对话；用户另指包名时以用户为准。")
        else:
            lines.append("- 当前前台包名：未读到（可再问或让用户刷新侧栏）。")
        lines.append("没有帧时间工具时禁止编造 FPS/CPU/GPU。")
        return "\n".join(lines)


register_role_context("device_perf", DevicePerfContext())
