"""真机 Unity Profiler：通知 kabu-profiler 写 .raw / 截图，再 pull 到 logs/profiler。

不冷启、不 force-stop。插件 HTTP（adb reverse 17890）不通时改写 cmd 文件。
历史按 thread_id 过滤（文件名 `{thread}__{serial}_{stamp}.raw`）。
详情读 .detaildata；对话只吃从它裁出的 .aidata。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.infra.app_logging import get_logger
from server.tools.android_panel import read_foreground
from server.tools.connect import get_android_serial

_log = get_logger("ailearning.profiler")

_lock = asyncio.Lock()
# 停录后的 pull 不占着 HTTP：大 .raw 要几分钟，请求若一直等，按钮就转圈
_pull_task: asyncio.Task | None = None
_state: dict[str, Any] = {
    "status": "idle",  # idle | recording | pulling | done | error
    "ok": False,
    "error": "",
    "serial": "",
    "package": "",
    "activity": "",
    "remote_path": "",
    "local_path": "",
    "started_at": "",
    "finished_at": "",
    "size_bytes": 0,
    "thread_id": "",
    "id": "",
    "summary": None,
}

_LAUNCHER_LINE = re.compile(
    r"(?P<pkg>[A-Za-z][\w.]*)/(?P<act>[A-Za-z][\w.$]*)"
)
# {thread}__{serial}_{YYYYMMDD}_{HHMMSS}.raw
_RAW_NAME_RE = re.compile(
    r"^(?P<thread>.+?)__(?P<serial>.+)_(?P<date>\d{8})_(?P<time>\d{6})\.raw$"
)
# 旧版无 thread：{serial}_{YYYYMMDD}_{HHMMSS}.raw
_RAW_LEGACY_RE = re.compile(
    r"^(?P<serial>.+)_(?P<date>\d{8})_(?P<time>\d{6})\.raw$"
)

_KNOWN_AREAS = (
    "Main Thread",
    "Render",
    "Scripts",
    "Physics",
    "Animation",
    "Audio",
    "Particles",
    "Lighting",
    "Network",
    "Loading",
    "VSync",
    "UI",
    "GC",
    "PlayerLoop",
)

# Player .raw 里偶发嵌入的 Editor/Player 版本串（如 2022.3.62f3 / 6000.4.5f1）
# 含中国版后缀 c1；大包 .raw 里版本串有时不在文件最开头
_UNITY_VER_RE = re.compile(rb"(?:20\d{2}|6000)\.\d+\.\d+[a-z]\d+(?:c\d+)?")
# 逐步加大窥视窗口：小文件一次够用；大包版本串可能落在 2MB 之后
_PEEK_VERSION_LIMITS = (2 * 1024 * 1024, 8 * 1024 * 1024, 16 * 1024 * 1024)


def _profiler_dir() -> Path:
    root = Path(__file__).resolve().parent.parent / "logs" / "profiler"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_thread_id(raw: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", (raw or "").strip())
    return (s[:96] if s else "") or ""


def _run_adb(serial: str, args: list[str], *, timeout: float) -> tuple[int, str, str]:
    import subprocess

    cmd = ["adb", "-s", serial, *args]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return 127, "", "未找到 adb"
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # 超时必须杀掉，否则大文件 pull 会在请求结束后继续写，历史里先冒出半份 .raw
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return 124, "", f"adb 超时：{' '.join(args[:4])}"
    return proc.returncode, out or "", err or ""


def _device_online(serial: str) -> bool:
    code, out, _ = _run_adb(serial, ["get-state"], timeout=5)
    return code == 0 and (out or "").strip() == "device"


def _resolve_launcher(serial: str, package: str) -> str:
    """返回 package/activity；失败则退回 Unity 默认 Activity。"""
    package = (package or "").strip()
    if not package:
        return ""

    code, out, _ = _run_adb(
        serial,
        ["shell", "cmd", "package", "resolve-activity", "--brief", package],
        timeout=8,
    )
    if code == 0:
        for line in (out or "").splitlines():
            line = line.strip()
            m = _LAUNCHER_LINE.search(line)
            if m and m.group("pkg") == package:
                return f"{m.group('pkg')}/{m.group('act')}"

    code2, out2, _ = _run_adb(
        serial,
        ["shell", "dumpsys", "package", package],
        timeout=15,
    )
    if code2 == 0 and out2:
        for m in re.finditer(
            r"([A-Za-z][\w.]+)/([A-Za-z][\w.$]+)\s*:[\s\S]*?"
            r"android.intent.action.MAIN[\s\S]*?"
            r"android.intent.category.LAUNCHER",
            out2,
        ):
            if m.group(1) == package:
                return f"{m.group(1)}/{m.group(2)}"

    return f"{package}/com.unity3d.player.UnityPlayerActivity"


def _remote_raw_path(package: str, stamp: str) -> str:
    safe_pkg = re.sub(r"[^A-Za-z0-9._-]+", "_", package or "app")[:64]
    return f"/sdcard/Download/ailearning_{safe_pkg}_{stamp}.raw"


def _sidecar_path(raw: Path) -> Path:
    return raw.with_suffix(raw.suffix + ".summary.json")


def _extract_unity_version(head: bytes) -> str:
    """从 .raw 字节里捞 Player 嵌入的版本；多命中时优先带 c1 的更长串。"""
    hits = [m.group(0).decode("ascii", errors="ignore") for m in _UNITY_VER_RE.finditer(head)]
    if not hits:
        return ""
    # 去重保序；同前缀时更长者（含 c1）优先
    seen: set[str] = set()
    ordered: list[str] = []
    for h in hits:
        if h in seen:
            continue
        seen.add(h)
        ordered.append(h)
    ordered.sort(key=lambda h: (len(h), h), reverse=True)
    return ordered[0]


def _read_raw_head(path: Path, size: int) -> tuple[bytes, str]:
    """读文件头：魔数/分区用首段；版本找不到再加大窗口（大包版本串常在 2MB 外）。"""
    head = b""
    ver = ""
    for limit in _PEEK_VERSION_LIMITS:
        need = min(limit, size)
        if len(head) >= need:
            ver = _extract_unity_version(head)
            if ver:
                return head, ver
            continue
        with path.open("rb") as f:
            head = f.read(need)
        ver = _extract_unity_version(head)
        if ver or need >= size:
            return head, ver
    return head, ver


def _peek_raw(path: Path) -> dict[str, Any]:
    """轻量窥视：魔数 + 体积 + 版本 + 扫到的 Profiler 分区名（非完整帧解析）。"""
    summary: dict[str, Any] = {
        "valid_magic": False,
        "size_bytes": 0,
        "unity_version": "",
        "areas": [],
        "notes": [],
    }
    try:
        summary["size_bytes"] = path.stat().st_size
        head, ver = _read_raw_head(path, summary["size_bytes"])
        if head.startswith(b"PD3U"):
            summary["valid_magic"] = True
        else:
            summary["notes"].append("文件头不是 PD3U，可能不是完整 Profiler .raw")
        summary["unity_version"] = ver
        if ver:
            summary["notes"].append(f"录制侧 Unity {ver}（打开 Profiler 请用同大版本 Editor）")
        else:
            summary["notes"].append(
                "未从 .raw 读到 Unity 版本串（不是没装 Editor；大包可再试或换同版本录制）"
            )
        found: list[str] = []
        # 分区名一般在很靠前；用已读 head 即可，避免为扫分区再读一遍
        for name in _KNOWN_AREAS:
            if name.encode("ascii") in head[: min(len(head), 2 * 1024 * 1024)]:
                found.append(name)
        summary["areas"] = found
        if found:
            summary["notes"].append("已扫到 Profiler 分区：" + "、".join(found[:8]))
        else:
            summary["notes"].append("未在文件头扫到常见分区名（仍可能有效）")
        if summary["valid_magic"]:
            summary["notes"].insert(0, "Unity Profiler binary（PD3U）")
    except Exception as e:
        summary["notes"].append(f"窥视失败：{e}")
    return summary


def _version_from_unity_path(exe: Path) -> str:
    """从安装目录名猜版本：…/Unity 2022.3.62f3c1/Editor/Unity.exe。"""
    for part in (exe.parent.parent.name, exe.parent.name):
        m = re.search(r"(20\d{2}\.\d+\.\d+[a-z]\d+(?:c\d+)?)", part or "", re.I)
        if m:
            return m.group(1)
    return ""


def _discover_local_editors() -> list[dict[str, str]]:
    """Hub 没登记的中国版（如 D:\\Work\\Unity\\2022.3.62f3c1）也要能找到。"""
    import os

    roots = [
        Path(r"D:\Work\Unity"),
        Path(r"C:\Program Files"),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            children = list(root.iterdir())
        except Exception:
            continue
        for child in children:
            name = child.name
            if not (
                name.startswith("Unity")
                or re.match(r"^20\d{2}\.\d+", name)
            ):
                continue
            exe = child / "Editor" / "Unity.exe"
            if not exe.is_file():
                # Unity Hub 标准：Unity/Hub/Editor/<ver>/Editor/Unity.exe
                if name == "Unity":
                    hub_editor = child / "Hub" / "Editor"
                    if hub_editor.is_dir():
                        try:
                            for ver_dir in hub_editor.iterdir():
                                hub_exe = ver_dir / "Editor" / "Unity.exe"
                                if hub_exe.is_file():
                                    key = _path_key(hub_exe)
                                    if key in seen:
                                        continue
                                    seen.add(key)
                                    ver = _version_from_unity_path(hub_exe) or ver_dir.name
                                    out.append({"version": ver, "exe": str(hub_exe)})
                        except Exception:
                            pass
                continue
            key = _path_key(exe)
            if key in seen:
                continue
            seen.add(key)
            ver = _version_from_unity_path(exe)
            if not ver:
                continue
            out.append({"version": ver, "exe": str(exe)})
    return out


def _hub_editors() -> list[dict[str, str]]:
    """Hub 登记 + 本机常见安装目录。只返回仍存在的 Unity.exe。"""
    import os

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    appdata = os.environ.get("APPDATA") or ""
    hub = Path(appdata) / "UnityHub" / "editors-v2.json"
    if hub.is_file():
        try:
            data = json.loads(hub.read_text(encoding="utf-8"))
        except Exception as e:
            _log.warning("读 Unity Hub editors 失败：%s", e)
            data = None
        rows = data.get("data") if isinstance(data, dict) else None
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ver = str(row.get("version") or "").strip()
                locs = row.get("location") or []
                if not ver or not isinstance(locs, list):
                    continue
                for loc in locs:
                    exe = Path(str(loc))
                    if not exe.is_file():
                        continue
                    key = _path_key(exe)
                    if key in seen:
                        break
                    seen.add(key)
                    out.append({"version": ver, "exe": str(exe)})
                    break
    for ed in _discover_local_editors():
        key = _path_key(ed["exe"])
        if key in seen:
            continue
        seen.add(key)
        out.append(ed)
    return out


def _version_key(ver: str) -> tuple[int, ...]:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", ver or "")
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _version_base(ver: str) -> str:
    return re.sub(r"c\d+$", "", (ver or "").strip(), flags=re.I)


def _running_unity_exes() -> set[str]:
    """当前正开着的 Unity.exe 路径，用来优先选打真机包的那份 Editor。"""
    found: set[str] = set()
    for _pid, cmd in _list_unity_command_lines():
        # 命令行首段常是带引号的 exe
        m = re.match(r'^"?([^"]+Unity\.exe)"?', cmd.strip(), re.I)
        if not m:
            continue
        try:
            found.add(_path_key(m.group(1)))
        except Exception:
            continue
    if not found:
        # 某些 Windows 环境无法读取 CIM CommandLine，但仍能查询进程可执行文件路径。
        import subprocess
        import sys
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-Process -Name Unity -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path"],
                    capture_output=True, text=True, timeout=10,
                )
                found.update(_path_key(line.strip()) for line in result.stdout.splitlines() if line.strip())
            except (OSError, subprocess.TimeoutExpired):
                pass
    return found


def _pick_best_editor(cands: list[dict[str, str]], match: str) -> dict[str, str]:
    if not cands:
        return {"match": "none", "version": "", "exe": ""}
    running = _running_unity_exes()

    def score(ed: dict[str, str]) -> tuple[int, int, str]:
        exe_key = _path_key(ed["exe"])
        # 正在跑 ClientExpress 的那份优先：.raw 多半是它打的包
        in_use = 1 if exe_key in running else 0
        # .raw 头常不带 c1，但中国版包要用 c1 Editor 才能 LoadProfile
        china = 1 if re.search(r"c\d+$", ed["version"], re.I) else 0
        return (in_use, china, ed["version"])

    best = sorted(cands, key=score, reverse=True)[0]
    return {"match": match, "version": best["version"], "exe": best["exe"]}


def _match_hub_editor(want: str) -> dict[str, str]:
    """精确版本 > 同号含 c1 > 同 major.minor；都没有则空。"""
    editors = _hub_editors()
    if not editors:
        return {"match": "none", "version": "", "exe": ""}
    want = (want or "").strip()
    if not want:
        # raw 未携带版本时，优先使用当前运行的唯一版本；否则仅在本机只有一个版本时尝试。
        running = _running_unity_exes()
        active = [ed for ed in editors if _path_key(ed["exe"]) in running]
        candidates = active or editors
        if len({ed["version"] for ed in candidates}) == 1:
            return _pick_best_editor(candidates, "fallback")
        return {"match": "none", "version": "", "exe": ""}
    if want:
        # .raw 头常是 2022.3.62f3，但真机包可能是 2022.3.62f3c1。
        # 同号的国际版/中国版要放进同一批再挑，不能先被 Hub 国际版 exact 截胡。
        base = _version_base(want)
        same_base = [
            ed
            for ed in editors
            if ed["version"] == want
            or ed["version"] == base
            or _version_base(ed["version"]) == base
        ]
        if same_base:
            return _pick_best_editor(same_base, "exact")
        wm = re.match(r"^(\d+)\.(\d+)", want)
        if wm:
            prefix = f"{wm.group(1)}.{wm.group(2)}."
            same = [ed for ed in editors if ed["version"].startswith(prefix)]
            if same:
                same.sort(key=lambda e: _version_key(e["version"]), reverse=True)
                top_key = _version_key(same[0]["version"])
                top = [ed for ed in same if _version_key(ed["version"]) == top_key]
                return _pick_best_editor(top, "minor")
    # 无录制版本线索时不瞎选，避免用错大版本
    return {"match": "none", "version": "", "exe": ""}


def _opener_template_dir() -> Path:
    return Path(__file__).resolve().parent / "profiler_opener_template"


def _opener_project_dir(editor_version: str) -> Path:
    """按 major.minor[+c1] 分目录。

    国际版 2022.3.62f3 和中国版 2022.3.62f3c1 不能共用 Library，
    否则冷启动能起来，LoadProfile 也会直接失败。
    """
    m = re.match(r"^(\d+)\.(\d+)", editor_version or "")
    folder = f"v{m.group(1)}_{m.group(2)}" if m else "v_unknown"
    china = re.search(r"(c\d+)$", editor_version or "", re.I)
    if china:
        folder = f"{folder}_{china.group(1).lower()}"
    root = Path(__file__).resolve().parent.parent / "data" / "profiler_opener" / folder
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ensure_opener_project(editor_version: str) -> Path:
    """同步模板脚本 + 写 ProjectVersion；版本变更时清 Library。"""
    import shutil

    project = _opener_project_dir(editor_version)
    tmpl = _opener_template_dir()
    # Assets/Editor
    editor_dir = project / "Assets" / "Editor"
    editor_dir.mkdir(parents=True, exist_ok=True)
    src_cs = tmpl / "Assets" / "Editor" / "AILearningProfilerOpen.cs"
    dst_cs = editor_dir / "AILearningProfilerOpen.cs"
    # 内容没变就别覆盖：开着的 Editor 会因此重编译，转换请求会丢
    if src_cs.is_file() and (not dst_cs.is_file() or src_cs.read_bytes() != dst_cs.read_bytes()):
        dst_cs.write_bytes(src_cs.read_bytes())
    # Packages
    pkg = project / "Packages"
    pkg.mkdir(parents=True, exist_ok=True)
    src_manifest = tmpl / "Packages" / "manifest.json"
    if src_manifest.is_file():
        shutil.copy2(src_manifest, pkg / "manifest.json")
    # ProjectVersion
    ps = project / "ProjectSettings"
    ps.mkdir(parents=True, exist_ok=True)
    ver_file = ps / "ProjectVersion.txt"
    prev = ""
    if ver_file.is_file():
        try:
            prev = ver_file.read_text(encoding="utf-8")
        except Exception:
            prev = ""
    new_body = f"m_EditorVersion: {editor_version}\nm_EditorVersionWithRevision: {editor_version}\n"
    if prev != new_body:
        ver_file.write_text(new_body, encoding="utf-8")
        # 换版本后旧 Library 会卡升级对话框，挡 executeMethod
        lib = project / "Library"
        if lib.is_dir():
            try:
                shutil.rmtree(lib)
            except Exception as e:
                _log.warning("清 profiler_opener Library 失败：%s", e)
    return project


def _reveal_in_explorer(path: Path) -> bool:
    import os
    import subprocess
    import sys

    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
            return True
        subprocess.Popen(["xdg-open", str(path.parent)])
        return True
    except Exception as e:
        _log.warning("资源管理器定位失败：%s", e)
        return False


def _copy_path_clipboard(path: Path) -> bool:
    import subprocess
    import sys

    text = str(path)
    try:
        if sys.platform == "win32":
            # 用 -LiteralPath 风格不可靠；直接传字符串值
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Set-Clipboard -Value $input",
                ],
                input=text,
                text=True,
                capture_output=True,
                timeout=8,
            )
            return completed.returncode == 0
        if sys.platform == "darwin":
            completed = subprocess.run(
                ["pbcopy"], input=text, text=True, capture_output=True, timeout=8
            )
            return completed.returncode == 0
        completed = subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text,
            text=True,
            capture_output=True,
            timeout=8,
        )
        return completed.returncode == 0
    except Exception as e:
        _log.warning("复制路径失败：%s", e)
        return False


def _resolve_session_raw(name: str = "", thread_id: str = "") -> tuple[Path | None, str]:
    """解析本会话 .raw；失败返回 (None, error)。"""
    want = _safe_thread_id(thread_id)
    raw = _resolve_raw_name(name) if name else latest_profiler_file()
    if raw is None and not name:
        local = str(_state.get("local_path") or "").strip()
        if local:
            p = Path(local)
            if p.is_file():
                raw = p
    if raw is None:
        return None, "没有可打开的 .raw"
    parsed = _parse_raw_name(raw.name)
    if want and (not parsed or parsed.get("thread_id") != want):
        return None, "该 .raw 不属于当前会话"
    return raw, ""


def _raw_unity_version(raw: Path) -> str:
    summary = _state.get("summary") if str(_state.get("id") or "") == raw.name else None
    if not isinstance(summary, dict) or not summary.get("unity_version"):
        summary = _peek_raw(raw)
    return str((summary or {}).get("unity_version") or "")


def _sibling_paths(raw: Path) -> dict[str, Path]:
    data = raw.with_suffix(".data")
    aidata = raw.with_suffix(".aidata")
    detail = raw.with_suffix(".detaildata")
    return {
        "data": data,
        "aidata": aidata,
        "detaildata": detail,
        # result 跟着 .detaildata：Editor 不再写 .aidata
        "result": Path(str(detail) + ".result.json"),
    }


_AIDATA_TOP_FRAMES = 15


def build_aidata_from_detaildata(detail: dict[str, Any]) -> dict[str, Any]:
    """从全帧 .detaildata 裁对话摘要。frames 整表不进 .aidata。"""
    frames = detail.get("frames") if isinstance(detail.get("frames"), list) else []
    ranked = sorted(
        (f for f in frames if isinstance(f, dict)),
        key=lambda f: float(f.get("cpu_ms") or 0),
        reverse=True,
    )
    top_frames: list[dict[str, Any]] = []
    spikes: list[dict[str, Any]] = []
    for f in ranked[:_AIDATA_TOP_FRAMES]:
        cpu_ms = round(float(f.get("cpu_ms") or 0), 3)
        item = {
            "frame": int(f.get("frame") or 0),
            "frame_ms": cpu_ms,
            "gpu_ms": round(float(f.get("gpu_ms") or 0), 3),
            "wait_ms": round(float(f.get("wait_ms") or 0), 3),
            "batches": f.get("batches"),
            "draw_calls": f.get("draw_calls"),
            "setpass": f.get("setpass"),
            "triangles": f.get("triangles"),
            "vertices": f.get("vertices"),
            "shadow_casters": f.get("shadow_casters"),
            "instanced_batches": f.get("instanced_batches"),
            "post_ms": f.get("post_ms"),
            "cpu_top": f.get("cpu_top") if isinstance(f.get("cpu_top"), list) else [],
            "cpu_self_top": f.get("cpu_self_top") if isinstance(f.get("cpu_self_top"), list) else [],
            "calls_top": f.get("calls_top") if isinstance(f.get("calls_top"), list) else [],
            "gc_top": f.get("gc_top") if isinstance(f.get("gc_top"), list) else [],
            "calls_total": int(f.get("calls_total") or 0),
            "gc_alloc_bytes": int(f.get("gc_alloc_bytes") or 0),
            "wait_top": f.get("wait_top") if isinstance(f.get("wait_top"), list) else [],
        }
        top_frames.append(item)
        spikes.append({"frame": item["frame"], "frame_ms": cpu_ms})
    overview = dict(detail.get("overview") or {}) if isinstance(detail.get("overview"), dict) else {}
    if "avg_frame_ms" not in overview and "avg_cpu_ms" in overview:
        overview["avg_frame_ms"] = overview["avg_cpu_ms"]
    overview["top_frame_count"] = len(top_frames)
    return {
        "schema": "ailearning.profiler.aidata/v1.3",
        "raw_path": detail.get("raw_path") or "",
        "data_path": detail.get("data_path") or "",
        "detaildata_path": detail.get("detaildata_path") or "",
        "unity_version": detail.get("unity_version") or "",
        "editor_version": detail.get("editor_version") or "",
        "generated_at": detail.get("generated_at") or "",
        "overview": overview,
        "top_frames": top_frames,
        "cpu": detail.get("cpu") if isinstance(detail.get("cpu"), dict) else {},
        "wait": detail.get("wait") if isinstance(detail.get("wait"), dict) else {},
        "gc": detail.get("gc") if isinstance(detail.get("gc"), dict) else {},
        "spikes": spikes,
        "notes": [
            "由 .detaildata 裁出的摘要；对话只用本文件",
            "详情面板读 .detaildata 全帧；这里只留最慢的帧",
        ],
    }


_AIDATA_CONTAINERS = frozenset(
    {
        "Main Thread",
        "PlayerLoop",
        "EditorLoop",
        "Profiler.Default",
        "Overhead",
    }
)


def select_profiler_frames(name: str, thread_id: str, numbers: list[int]) -> dict[str, Any]:
    """从本会话全帧数据生成指定帧摘要；不启动 Editor 或改变原始采集。"""
    raw, error = _resolve_session_raw(name, thread_id)
    if raw is None:
        raise ValueError(error)
    if not numbers or len(numbers) > 100 or any(type(n) is not int or n < 0 for n in numbers):
        raise ValueError("请输入 1 至 100 个非负整数帧号")
    path = raw.with_suffix(".detaildata")
    if not path.is_file():
        raise ValueError("请先生成分析数据")
    detail = json.loads(path.read_text(encoding="utf-8"))
    wanted = list(dict.fromkeys(numbers))
    indexed = {f["frame"]: f for f in detail.get("frames", []) if isinstance(f, dict) and "frame" in f}
    missing = [n for n in wanted if n not in indexed]
    if missing:
        raise ValueError("当前记录不存在帧：" + ", ".join(map(str, missing)))
    # 单独生成每帧摘要，避免按 CPU 排序或 Top 数量上限截掉用户指定的帧。
    selected = [build_aidata_from_detaildata({"frames": [indexed[n]]})["top_frames"][0] for n in wanted]
    aidata_path = raw.with_suffix(".aidata")
    aidata = _load_aidata_file(aidata_path) or build_aidata_from_detaildata(detail)
    aidata["custom_frames"] = selected
    detail["custom_frame_numbers"] = wanted
    # 先完整写临时文件再替换，避免中断把全帧数据截成半份。
    for target, payload in ((aidata_path, aidata), (path, detail)):
        pending = target.with_suffix(target.suffix + ".tmp")
        pending.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        pending.replace(target)
    if str(_state.get("id") or "") == raw.name:
        _state["aidata"] = aidata
        _state["aidata_path"] = str(aidata_path)
    return {"ok": True, "frames": wanted}


def _aidata_marker_bucket(name: str) -> str:
    """与 Editor 脚本 ClassifyMarker 对齐：container | wait | work。"""
    n = name or ""
    if n in _AIDATA_CONTAINERS:
        return "container"
    low_rules = (
        "WaitForPresent",
        "WaitForLastPresent",
        "Gfx.WaitForPresent",
        "Semaphore.Wait",
        "WaitForTargetFPS",
        "WaitForLastPresentation",
        "WaitForJobGroupID",
        "Gfx.WaitFor",
        "PresentWait",
    )
    if any(r.lower() in n.lower() for r in low_rules) or n.startswith("WaitFor"):
        return "wait"
    return "work"


def refine_aidata_v11(aidata: dict[str, Any], *, persist_path: Path | None = None) -> dict[str, Any]:
    """把旧 v1 扁平 top_markers 升到 v1.1（滤容器 + wait 桶）。已是 v1.1 则原样返回。"""
    if not isinstance(aidata, dict):
        return aidata
    schema = str(aidata.get("schema") or "")
    if "v1.1" in schema and isinstance(aidata.get("wait"), dict):
        return aidata

    cpu = aidata.get("cpu") if isinstance(aidata.get("cpu"), dict) else {}
    raw_tops = cpu.get("top_markers") if isinstance(cpu.get("top_markers"), list) else []
    # 已有 wait 且 top 里已无容器 → 只抬 schema
    already_split = isinstance(aidata.get("wait"), dict) and not any(
        _aidata_marker_bucket(str(m.get("name") or "")) == "container"
        for m in raw_tops
        if isinstance(m, dict)
    )
    if already_split:
        aidata = {**aidata, "schema": "ailearning.profiler.aidata/v1.1"}
        if persist_path is not None:
            try:
                persist_path.write_text(
                    json.dumps(aidata, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                _log.warning("写 refined aidata 失败：%s", e)
        return aidata

    work: list[dict[str, Any]] = []
    wait: list[dict[str, Any]] = []
    containers: list[str] = []
    wait_total = 0.0
    for m in raw_tops:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or "")
        bucket = _aidata_marker_bucket(name)
        if bucket == "container":
            if name and name not in containers:
                containers.append(name)
            continue
        entry = {
            "name": name,
            "total_ms": float(m.get("total_ms") or 0),
            "hits": int(m.get("hits") or 0),
        }
        if bucket == "wait":
            wait.append(entry)
            wait_total += entry["total_ms"]
        else:
            work.append(entry)

    work.sort(key=lambda x: x["total_ms"], reverse=True)
    wait.sort(key=lambda x: x["total_ms"], reverse=True)
    notes = list(aidata.get("notes") or []) if isinstance(aidata.get("notes"), list) else []
    bump = [
        "v1.1：cpu.top_markers 已滤 Main Thread/PlayerLoop 等容器",
        "Wait/Present/限帧进 wait 桶；对话优先看 cpu 再看 wait",
    ]
    for line in bump:
        if line not in notes:
            notes.insert(0, line)

    out = {
        **aidata,
        "schema": "ailearning.profiler.aidata/v1.1",
        "cpu": {
            **cpu,
            "top_markers": work[:40],
            "filtered_containers": containers,
        },
        "wait": {
            "total_ms": round(wait_total, 3),
            "top_markers": wait[:20],
        },
        "notes": notes,
    }
    if persist_path is not None:
        try:
            persist_path.write_text(
                json.dumps(out, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            _log.warning("写 refined aidata 失败：%s", e)
    return out


def _load_aidata_file(path: Path) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    schema = str(obj.get("schema") or "")
    # v1.2/v1.3 已分桶；再跑 v1.1 升级会把 schema 写回旧版
    if "v1.2" in schema or "v1.3" in schema:
        return obj
    return refine_aidata_v11(obj, persist_path=path)


def _path_key(path: Path | str) -> str:
    """路径比对用：大小写、斜杠、尾部分隔符都抹平。"""
    try:
        text = str(Path(path).resolve())
    except Exception:
        text = str(path)
    return text.replace("/", "\\").rstrip("\\").lower()


def _cmd_has_project(cmd: str, project: Path) -> bool:
    if not cmd:
        return False
    return _path_key(project) in cmd.replace("/", "\\").lower()


def _opener_pid(project: Path) -> int:
    info = project / "Library" / "EditorInstance.json"
    if not info.is_file():
        return 0
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
        return int(data.get("process_id") or 0)
    except Exception:
        return 0


def _pid_alive(pid: int) -> bool:
    import os
    import sys

    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        # SYNCHRONIZE：只要进程还在就打得开，不要求能调试它
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_command_line(pid: int) -> str:
    import subprocess
    import sys

    if pid <= 0:
        return ""
    if sys.platform == "win32":
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"(Get-CimInstance Win32_Process | "
                    f"Where-Object {{ $_.ProcessId -eq {int(pid)} }}).CommandLine"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return (completed.stdout or "").strip()
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except Exception:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace")


def _process_image_name(pid: int) -> str:
    import subprocess
    import sys

    if pid <= 0:
        return ""
    if sys.platform == "win32":
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"(Get-CimInstance Win32_Process | "
                    f"Where-Object {{ $_.ProcessId -eq {int(pid)} }}).Name"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return (completed.stdout or "").strip()
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _list_unity_command_lines() -> list[tuple[int, str]]:
    """(pid, command_line)。Windows 上不用 -Filter 字符串，避免引号被拆坏。"""
    import subprocess
    import sys

    if sys.platform == "win32":
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { $_.Name -eq 'Unity.exe' } | "
                    "Select-Object ProcessId, CommandLine | "
                    "ConvertTo-Json -Compress"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw = (completed.stdout or "").strip()
        if not raw:
            return []
        try:
            rows = json.loads(raw)
        except Exception:
            return []
        if isinstance(rows, dict):
            rows = [rows]
        out: list[tuple[int, str]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            try:
                pid = int(row.get("ProcessId") or 0)
            except Exception:
                continue
            if pid > 0:
                out.append((pid, str(row.get("CommandLine") or "")))
        return out
    out = []
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                    "utf-8", "replace"
                )
            except Exception:
                continue
            if "Unity" not in cmdline and "unity" not in cmdline:
                continue
            out.append((int(entry.name), cmdline))
    except Exception:
        pass
    return out


def _find_opener_unity_pid(project: Path) -> int:
    """按命令行找真正开着这个旁路工程的 Unity。EditorInstance 可能是死 pid / 复用号。"""
    want = _path_key(project)
    for pid, cmd in _list_unity_command_lines():
        if want in cmd.replace("/", "\\").lower():
            return pid
    return 0


def _project_lock_held(project: Path) -> bool:
    """UnityLockfile 被独占时工程一定还开着。命令行扫空也不能当成已退出。"""
    import sys

    path = project / "Temp" / "UnityLockfile"
    if not path.is_file() or sys.platform != "win32":
        return False
    import ctypes

    kernel = ctypes.windll.kernel32
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    # 不共享：能独占打开说明没人拿着；共享冲突说明 Unity 还开着这个工程。
    handle = kernel.CreateFileW(str(path), 0x80000000 | 0x40000000, 0, None, 3, 0x80, None)
    invalid = ctypes.c_void_p(-1).value
    if handle and handle != invalid:
        kernel.CloseHandle(handle)
        return False
    return kernel.GetLastError() in (32, 33)


def _clear_stale_opener_locks(project: Path) -> None:
    """Unity 已退但 EditorInstance / UnityLockfile 还在时，冷启动会弹「工程已打开」。"""
    for rel in (
        Path("Library") / "EditorInstance.json",
        Path("Temp") / "UnityLockfile",
    ):
        path = project / rel
        try:
            path.unlink(missing_ok=True)
        except Exception as e:
            _log.info("清旁路锁文件失败 %s：%s", path, e)


def _resolve_opener_unity_pid(project: Path) -> int:
    """真正开着这个工程的 Unity pid；对不上就当没开，并清掉陈旧标记。"""
    live = _find_opener_unity_pid(project)
    if live > 0:
        return live
    claimed = _opener_pid(project)
    if claimed > 0 and _pid_alive(claimed):
        name = _process_image_name(claimed).lower()
        cmd = _process_command_line(claimed)
        # Windows 会复用 pid：EditorInstance 里的号可能已经是别的进程。
        if name == "unity.exe" and _cmd_has_project(cmd, project):
            return claimed
    # 命令行对不上，但锁还被占用：工程开着。返回 -1，避免拿复用 pid 去 taskkill。
    if _project_lock_held(project):
        _log.info("旁路 UnityLockfile 仍被占用，视为已打开：%s", project)
        return -1
    if claimed > 0 or (project / "Temp" / "UnityLockfile").is_file():
        _log.info(
            "旁路 EditorInstance/锁文件已过期（claimed=%s），清掉后冷启动：%s",
            claimed,
            project,
        )
        _clear_stale_opener_locks(project)
    return 0


def _opener_editor_running(project: Path) -> bool:
    """这个旁路工程是否真有 Unity 开着。

    不能只看 EditorInstance 的 pid：进程退出后号会被系统复用，
    再 Popen 会被挡，转换请求也没人收。
    """
    # >0 是对上的 Unity；-1 是锁被占用但没对上 pid。两种都算开着。
    return _resolve_opener_unity_pid(project) != 0


def _opener_script_needs_update(project: Path) -> bool:
    """磁盘上的 Editor 脚本和模板不一致。已开着的域不会自己换成新轮询。"""
    src = _opener_template_dir() / "Assets" / "Editor" / "AILearningProfilerOpen.cs"
    dst = project / "Assets" / "Editor" / "AILearningProfilerOpen.cs"
    if not src.is_file():
        return False
    if not dst.is_file():
        return True
    try:
        return src.read_bytes() != dst.read_bytes()
    except Exception:
        return True


def _stop_opener_editor(project: Path) -> tuple[bool, str]:
    """只结束这个旁路工程的 Unity。陈旧 EditorInstance 直接清掉，不动别的工程。"""
    import subprocess
    import sys

    pid = _resolve_opener_unity_pid(project)
    if pid < 0:
        return False, "旁路工程开着，但没对上 Unity 进程，没有结束它。"
    if pid <= 0:
        _clear_stale_opener_locks(project)
        return True, ""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    else:
        import os
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as e:
            return False, f"结束旁路 Unity 失败：{e}"
    for _ in range(40):
        if not _pid_alive(pid):
            break
        time.sleep(0.25)
    if _pid_alive(pid):
        return False, "旁路 Unity 还没退出，没有再启动第二个。"
    _clear_stale_opener_locks(project)
    return True, ""


def _relaunch_opener_convert(project: Path, exe: str) -> tuple[bool, str]:
    """已开着的窗口没吃掉请求时，关掉它再冷启动，让 executeMethod 真正跑转换。"""
    import subprocess

    if not exe:
        return False, "没有匹配的 Unity，无法重新启动转换。"
    stopped, err = _stop_opener_editor(project)
    if not stopped:
        return False, err
    pending_path = project / "open_pending.json"
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except Exception:
        return False, "缺少 open_pending.json，无法重新启动转换。"
    if not isinstance(pending, dict):
        return False, "open_pending.json 无效，无法重新启动转换。"
    # 投递时把它改成了 open，避免人再点「打开」会转完退出。冷启动必须改回 convert。
    pending["mode"] = "convert"
    pending["frame"] = -1
    pending_path.write_text(
        json.dumps(pending, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        (project / "convert_request.json").unlink(missing_ok=True)
    except Exception:
        pass
    subprocess.Popen(
        [
            exe,
            "-projectPath",
            str(project),
            "-executeMethod",
            "AILearning.ProfilerOpen.RunPending",
        ],
        cwd=str(project),
    )
    _log.info("已关掉没响应的旁路 Unity，重新冷启动转换：%s", project)
    return True, ""


def _launch_unity_pending(
    *,
    raw: Path,
    unity_ver: str,
    matched: dict[str, str],
    mode: str,
    data_path: Path | None = None,
    aidata_path: Path | None = None,
    result_path: Path | None = None,
    detaildata_path: Path | None = None,
    frame: int = -1,
) -> tuple[bool, str, str]:
    """写 open_pending 并 Popen Unity；返回 (ok, project_path, error)。"""
    import subprocess

    if matched.get("match") == "none" or not matched.get("exe"):
        return False, "", (
            f"本机未找到与录制版本 {unity_ver} 匹配的 Editor。"
            if unity_ver
            else "未能从 .raw 读出 Unity 版本，无法自动匹配 Editor。"
        )
    try:
        project_dir = _opener_project_dir(matched["version"])
        editor_open = _opener_editor_running(project_dir)
        # 先关再覆盖脚本。窗口开着时改 .cs 不会立刻重编译（没焦点就不刷新），
        # 旧域里没有 TryConsumeConvertRequest，请求文件会一直躺着。
        if mode == "convert" and editor_open and _opener_script_needs_update(project_dir):
            _log.info(
                "旁路工程已打开但脚本不是当前这份，关掉后冷启动：%s",
                project_dir,
            )
            stopped, stop_err = _stop_opener_editor(project_dir)
            if not stopped:
                return False, str(project_dir), stop_err or "旁路 Unity 没能关掉，无法重新启动转换。"
            editor_open = False
        project = _ensure_opener_project(matched["version"])
        pending: dict[str, Any] = {
            "mode": mode,
            "raw_path": str(raw.resolve()),
            "unity_version": unity_ver,
            "editor_version": matched["version"],
            "data_path": str(data_path.resolve()) if data_path else "",
            "detaildata_path": str(detaildata_path.resolve()) if detaildata_path else "",
            "aidata_path": str(aidata_path.resolve()) if aidata_path else "",
            "result_path": str(result_path.resolve()) if result_path else "",
            # 缺省 0 会被当成第 0 帧；-1 表示只打开
            "frame": int(frame),
        }
        (project / "open_pending.json").write_text(
            json.dumps(pending, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 工程已开着时第二次启动不会重跑 executeMethod。
        # convert 改投递 convert_request.json，由已开着的窗口在 update 里做，并且不退出。
        if mode == "convert" and editor_open:
            # nonce 用字符串：JsonUtility 对 long 会读成 0，请求会被当成无效文件丢掉。
            (project / "convert_request.json").write_text(
                json.dumps(
                    {**pending, "nonce": str(int(time.time() * 1000))},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            # 留给下次冷启动的仍是 open。若留成 convert，人再点「打开」会转完直接退出。
            pending["mode"] = "open"
            pending["frame"] = -1
            (project / "open_pending.json").write_text(
                json.dumps(pending, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _log.info("旁路工程已打开，投递转换请求而不再启动第二个 Unity：%s", project)
            return True, str(project), ""
        # 冷启动走 executeMethod。磁盘上若还留着上次的请求，轮询会再转一遍。
        if mode == "convert":
            try:
                (project / "convert_request.json").unlink(missing_ok=True)
            except Exception:
                pass
        if not editor_open:
            # 死掉的 EditorInstance / 空 UnityLockfile 会让新 Unity 以为工程还开着。
            _clear_stale_opener_locks(project)
        # 工程已开着时第二次启动不会重跑 executeMethod，靠这个文件挪光标。
        # 再 Popen 会弹「另一个 Unity 已打开此工程」，定位就停在这个对话框上。
        if int(frame) >= 0:
            (project / "jump_frame.json").write_text(
                json.dumps(
                    {
                        "raw_path": str(raw.resolve()),
                        "frame": int(frame),
                        # 必须是字符串：JsonUtility 把 long 读成 0，已开着的窗口会丢掉这次定位。
                        "nonce": str(int(time.time() * 1000)),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        if editor_open:
            _log.info(
                "旁路工程已打开，只投递定位、不再启动第二个 Unity：%s frame=%s",
                project,
                int(frame),
            )
            return True, str(project), ""
        # convert 也不用 -batchmode：部分版本 ProfilerDriver 在 batch 下不稳；脚本末尾 Exit
        subprocess.Popen(
            [
                matched["exe"],
                "-projectPath",
                str(project),
                "-executeMethod",
                "AILearning.ProfilerOpen.RunPending",
            ],
            cwd=str(project),
        )
        return True, str(project), ""
    except Exception as e:
        return False, "", f"启动 Unity 失败：{e}"


def open_profiler_in_editor(
    name: str = "",
    thread_id: str = "",
    frame: int = -1,
) -> dict[str, Any]:
    """用本机匹配版本的 Unity Editor 打开 Profiler 并 Load 该 .raw。

    frame>=0 时把 Profiler 光标移到该帧。已开着的窗口靠 jump_frame.json，
    不靠第二次 executeMethod。
    """
    raw, err0 = _resolve_session_raw(name, thread_id)
    if raw is None:
        return {
            "ok": False,
            "error": err0,
            "id": name or "",
            "local_path": "",
            "unity_version": "",
            "matched_version": "",
            "matched_exe": "",
            "match": "none",
            "revealed": False,
            "clipboard": False,
            "launched": False,
            "project_path": "",
            "hint": "",
        }

    unity_ver = _raw_unity_version(raw)
    matched = _match_hub_editor(unity_ver)
    want_frame = int(frame)
    # 定位某一帧时不要弹出资源管理器，否则每点一次热点帧就跳一次窗口
    revealed = False if want_frame >= 0 else _reveal_in_explorer(raw)
    clipboard = False if want_frame >= 0 else _copy_path_clipboard(raw)
    launched, project_path, err = _launch_unity_pending(
        raw=raw,
        unity_ver=unity_ver,
        matched=matched,
        mode="open",
        frame=want_frame,
    )
    hint = ""
    if want_frame >= 0 and launched:
        hint = (
            f"已请求把 Profiler 光标移到帧 {want_frame}。"
            "窗口已开着会直接跳；否则先拉起 Unity（首次要编译）。"
        )
    elif want_frame >= 0:
        hint = "没能拉起 Unity，无法把光标移到这一帧。"
    elif not launched:
        hint = "已在资源管理器中定位并复制路径；请手动用同大版本 Unity：Profiler → Load。"
    elif matched.get("match") == "fallback":
        hint = f"raw 未包含版本号，尝试使用本机 Unity {matched['version']} 打开；兼容性以 Unity 加载结果为准。"
    elif matched.get("match") == "exact":
        hint = (
            f"已用 Unity {matched['version']} 打开旁路工程并尝试 Load。"
            "首次会编译 Library，稍等片刻。"
        )
    else:
        hint = (
            f"录制为 {unity_ver}，本机用同系列 {matched['version']} 打开（非精确版本）。"
            "若 Load 失败请安装与真机包一致的 Editor。"
        )

    return {
        "ok": launched or revealed,
        "error": err,
        "id": raw.name,
        "local_path": str(raw),
        "unity_version": unity_ver,
        "matched_version": matched.get("version") or "",
        "matched_exe": matched.get("exe") or "",
        "match": matched.get("match") or "none",
        "revealed": revealed,
        "clipboard": clipboard,
        "launched": launched,
        "project_path": project_path,
        "hint": hint,
    }


def reveal_profiler_data(name: str = "", thread_id: str = "") -> dict[str, Any]:
    """打开这条 .raw 所在的 profiler 目录，并选中该文件（旁边就是 .data / .detaildata）。"""
    raw, err0 = _resolve_session_raw(name, thread_id)
    if raw is None:
        return {"ok": False, "error": err0 or "没有可打开的 .raw", "path": ""}
    ok = _reveal_in_explorer(raw)
    return {
        "ok": ok,
        "error": "" if ok else "无法打开资源管理器",
        "path": str(raw.parent),
    }


def convert_profiler_analysis(
    name: str = "",
    thread_id: str = "",
    timeout_sec: float = 600,
) -> dict[str, Any]:
    """一键：.raw → Editor 写 .data + .detaildata，服务端再裁 .aidata。

    详情用 .detaildata。对话只吃 .aidata，勿把 .raw/.data/.detaildata 全文塞进模型。
    """
    raw, err0 = _resolve_session_raw(name, thread_id)
    empty = {
        "ok": False,
        "error": err0 or "",
        "id": name or "",
        "local_path": "",
        "data_path": "",
        "detaildata_path": "",
        "aidata_path": "",
        "unity_version": "",
        "matched_version": "",
        "match": "none",
        "frame_count": 0,
        "hint": "",
    }
    if raw is None:
        return empty

    paths = _sibling_paths(raw)
    unity_ver = _raw_unity_version(raw)
    matched = _match_hub_editor(unity_ver)
    if matched.get("match") == "none" or not matched.get("exe"):
        if unity_ver:
            err = (
                f"本机未找到与录制版本 {unity_ver} 匹配的 Editor，无法生成 .data/.detaildata。"
            )
            hint = (
                "请安装同大版本 Unity（中国版包优先 c1），"
                "或确认 Hub/常见目录里已有对应 Editor。"
            )
        else:
            err = (
                "未能从这份 .raw 读出 Unity 版本，无法自动匹配 Editor 生成 .data/.detaildata。"
            )
            hint = (
                "不是「没装 2022.3」：当前 .raw 头里没扫到版本串。"
                "请打开打真机包的 Unity Editor，并关闭其他版本的 Editor 后重试。"
            )
        return {
            **empty,
            "id": raw.name,
            "local_path": str(raw),
            "unity_version": unity_ver,
            "match": "none",
            "error": err,
            "hint": hint,
        }

    # 清旧 result，避免读到上次成功残留
    try:
        paths["result"].unlink(missing_ok=True)
    except Exception:
        pass

    already_open = _opener_editor_running(_opener_project_dir(matched["version"]))
    launched, project_path, err = _launch_unity_pending(
        raw=raw,
        unity_ver=unity_ver,
        matched=matched,
        mode="convert",
        data_path=paths["data"],
        detaildata_path=paths["detaildata"],
        result_path=paths["result"],
        frame=-1,
    )
    if not launched:
        return {
            **empty,
            "id": raw.name,
            "local_path": str(raw),
            "unity_version": unity_ver,
            "matched_version": matched.get("version") or "",
            "match": matched.get("match") or "none",
            "error": err or "启动 Unity 失败",
            "hint": "首次旁路工程会编译 Library，也可先点「用 Unity Profiler 打开」预热。",
        }

    deadline = time.time() + max(30.0, float(timeout_sec or 600))
    project = Path(project_path) if project_path else None
    # 请求文件还在 = 已经开着的窗口还没开始。十几秒内不删，就当这个域不会轮询。
    handed_off = bool(project and (project / "convert_request.json").is_file())
    consume_by = time.time() + 15.0
    relaunched = False
    result: dict[str, Any] | None = None
    while time.time() < deadline:
        if paths["result"].is_file():
            try:
                # 文件可能还在写：体积过小则再等
                if paths["result"].stat().st_size < 20:
                    time.sleep(0.4)
                    continue
                result = json.loads(paths["result"].read_text(encoding="utf-8"))
                if isinstance(result, dict) and ("ok" in result or "error" in result):
                    break
            except Exception:
                time.sleep(0.5)
                continue
        if (
            handed_off
            and not relaunched
            and project is not None
            and time.time() >= consume_by
            and (project / "convert_request.json").is_file()
        ):
            _log.warning(
                "已打开的旁路 Unity 没有开始转换，关掉后重新启动：%s",
                project,
            )
            ok_re, err_re = _relaunch_opener_convert(
                project, str(matched.get("exe") or "")
            )
            relaunched = True
            handed_off = False
            if not ok_re:
                return {
                    **empty,
                    "id": raw.name,
                    "local_path": str(raw),
                    "unity_version": unity_ver,
                    "matched_version": matched.get("version") or "",
                    "match": matched.get("match") or "none",
                    "error": err_re,
                    "project_path": project_path,
                    "hint": "旁路窗口没响应。确认关掉 v2022_3 那个 Unity 后再点一次。",
                }
        time.sleep(1.0)
    else:
        if relaunched:
            hint = "已经关掉没响应的旁路窗口并重新启动过，仍没有写出分析数据。可再点一次。"
        elif already_open:
            hint = "旁路 Unity 已经开着，但没有开始转换。先关掉 v2022_3 那个窗口再点一次。"
        else:
            hint = "可先「用 Unity Profiler 打开」完成一次 Library 编译后再生成。"
        return {
            "ok": False,
            "error": f"等待 Unity 转换超时（{int(timeout_sec)}s）。首次编译旁路工程可能更久，请重试。",
            "id": raw.name,
            "local_path": str(raw),
            "data_path": str(paths["data"]),
            "detaildata_path": str(paths["detaildata"]),
            "aidata_path": str(paths["aidata"]),
            "unity_version": unity_ver,
            "matched_version": matched.get("version") or "",
            "match": matched.get("match") or "none",
            "frame_count": 0,
            "project_path": project_path,
            "hint": hint,
        }

    ok = bool((result or {}).get("ok"))
    data_path = str((result or {}).get("data_path") or paths["data"])
    detail_path = str((result or {}).get("detaildata_path") or paths["detaildata"])
    aidata_path = str(paths["aidata"])
    frame_count = int((result or {}).get("frame_count") or 0)
    # Editor 只保证 .data + .detaildata；.aidata 在下面裁
    if ok and (not Path(data_path).is_file() or not Path(detail_path).is_file()):
        ok = False
        err_msg = str((result or {}).get("error") or "") or "Unity 回报成功但未找到 .data/.detaildata"
    else:
        err_msg = str((result or {}).get("error") or "")

    aidata_obj: dict[str, Any] | None = None
    if ok:
        try:
            detail_obj = json.loads(Path(detail_path).read_text(encoding="utf-8"))
            if not isinstance(detail_obj, dict):
                raise ValueError(".detaildata 不是对象")
            aidata_obj = build_aidata_from_detaildata(detail_obj)
            Path(aidata_path).write_text(
                json.dumps(aidata_obj, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            overview = aidata_obj.get("overview") if isinstance(aidata_obj.get("overview"), dict) else {}
            frame_count = int(overview.get("frame_count") or frame_count)
        except Exception as e:
            ok = False
            aidata_obj = None
            err_msg = f"从 .detaildata 裁 .aidata 失败：{e}"

    # 详情正文不进 state：.detaildata 可能几十 MB，轮询 latest 不能带着它
    if ok and str(_state.get("id") or "") == raw.name:
        _state["data_path"] = data_path
        _state["detaildata_path"] = detail_path
        _state["aidata_path"] = aidata_path
        _state["status"] = "done"
        _state["ok"] = True
        # 打开历史时留下的「上次没跑完」不能盖住这次成功
        _state["error"] = ""
        if aidata_obj is not None:
            _state["aidata"] = aidata_obj
        _save_sidecar(dict(_state))

    hint = ""
    if ok:
        hint = (
            f"已生成 .data + .detaildata（{frame_count} 帧，Editor {matched.get('version')}），"
            "并裁出 .aidata。详情用 .detaildata。"
        )
    elif matched.get("match") == "fallback":
        hint = f"raw 未包含版本号，本次尝试 Unity {matched.get('version')}；若加载失败，请使用录制时的 Editor。"
    elif matched.get("match") == "minor":
        hint = f"录制 {unity_ver} / 使用 {matched.get('version')}（同系列非精确），失败时请装一致版本。"

    return {
        "ok": ok,
        "error": err_msg,
        "id": raw.name,
        "local_path": str(raw),
        "data_path": data_path if Path(data_path).is_file() else "",
        "detaildata_path": detail_path if Path(detail_path).is_file() else "",
        "aidata_path": aidata_path if Path(aidata_path).is_file() else "",
        "unity_version": unity_ver,
        "matched_version": matched.get("version") or "",
        "match": matched.get("match") or "none",
        "frame_count": frame_count,
        "project_path": project_path,
        "hint": hint,
    }


def _save_sidecar(payload: dict[str, Any]) -> None:
    path = str(payload.get("local_path") or "").strip()
    if not path:
        return
    raw = Path(path)
    if not raw.is_file():
        return
    try:
        data = dict(payload)
        data["id"] = raw.name
        _sidecar_path(raw).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        _log.warning("写 Profiler sidecar 失败：%s", e)


def _parse_raw_name(name: str) -> dict[str, str] | None:
    m = _RAW_NAME_RE.match(name or "")
    if m:
        return {
            "thread_id": m.group("thread"),
            "serial": m.group("serial"),
            "date": m.group("date"),
            "time": m.group("time"),
        }
    return None


def _resolve_raw_name(name: str) -> Path | None:
    raw = (name or "").strip().replace("\\", "/")
    base = Path(raw).name
    if not base.endswith(".raw"):
        return None
    if base != raw and "/" in raw:
        return None
    file = _profiler_dir() / base
    if file.is_file() and file.stat().st_size > 0:
        return file
    return None


def _start_via_device_script(
    serial: str, component: str, unity_args: str
) -> tuple[int, str, str]:
    """把 am start 写成手机脚本再执行，避免 Windows adb 拆掉带空格的 extra。"""
    import tempfile

    script = (
        "#!/system/bin/sh\n"
        f'am start -S -n {component} --es unity "{unity_args}"\n'
    )
    remote_sh = "/data/local/tmp/ailearning_profiler_start.sh"
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".sh",
        delete=False,
        newline="\n",
        encoding="ascii",
    ) as tmp:
        tmp.write(script)
        local_sh = tmp.name
    try:
        code_p, out_p, err_p = _run_adb(
            serial, ["push", local_sh, remote_sh], timeout=15
        )
        if code_p != 0:
            return code_p, out_p, err_p or "push start 脚本失败"
        return _run_adb(serial, ["shell", "sh", remote_sh], timeout=20)
    finally:
        try:
            Path(local_sh).unlink(missing_ok=True)
        except Exception:
            pass
        _run_adb(serial, ["shell", "rm", "-f", remote_sh], timeout=5)


async def import_profiler_raw(request, name: str, thread_id: str) -> dict[str, Any]:
    """流式接收外部 raw，登记到当前会话；转换继续复用原有入口。"""
    from fastapi import HTTPException

    tid = _safe_thread_id(thread_id)
    original = Path(name.replace("\\", "/")).name
    if not tid or not original.lower().endswith(".raw"):
        raise HTTPException(400, "请选择 .raw 文件，并指定当前会话")
    async with _lock:
        if _state.get("status") in ("recording", "pulling", "converting"):
            raise HTTPException(409, "Profiler 正在录制或处理数据，请完成后再导入")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        raw = _profiler_dir() / f"{tid}__import-{uuid4().hex}_{stamp}.raw"
        pending = raw.with_suffix(".upload")
        try:
            total = 0
            # 分块写盘，避免大型 raw 整体占用内存；未完成的文件不进入历史。
            with pending.open("xb") as output:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > 2 * 1024**3:
                        raise HTTPException(413, "文件超过 2 GiB")
                    await asyncio.to_thread(output.write, chunk)
            summary = await asyncio.to_thread(_peek_raw, pending)
            if not total:
                raise HTTPException(400, "文件为空，请选择非空的 Unity Profiler .raw 文件")
            # 文件头只作提示；不同来源的 raw 是否可读交给 Unity 转换流程判断。
            pending.rename(raw)
            payload = {
                "ok": True, "error": "", "status": "done",
                "serial": "", "package": "", "activity": "", "remote_path": "",
                "local_path": str(raw), "id": raw.name, "thread_id": tid,
                "started_at": "", "finished_at": datetime.now(timezone.utc).isoformat(),
                "size_bytes": total, "summary": summary,
                "source": "import", "original_name": original,
            }
            # 登记失败必须报错，不能向用户宣称已加入历史。
            _sidecar_path(raw).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            _state.clear()
            _state.update(payload)
            return latest_profiler(tid)
        except BaseException:
            raw.unlink(missing_ok=True)
            _sidecar_path(raw).unlink(missing_ok=True)
            raise
        finally:
            pending.unlink(missing_ok=True)


def latest_profiler(thread_id: str = "") -> dict[str, Any]:
    want = _safe_thread_id(thread_id)
    cur = _safe_thread_id(str(_state.get("thread_id") or ""))
    if want and cur and want != cur:
        return {
            "ok": False,
            "error": "",
            "status": "idle",
            "serial": "",
            "package": "",
            "activity": "",
            "remote_path": "",
            "local_path": "",
            "started_at": "",
            "finished_at": "",
            "size_bytes": 0,
            "thread_id": want,
            "id": "",
            "summary": None,
            "data_path": "",
            "detaildata_path": "",
            "aidata_path": "",
            "aidata": None,
            "shots": [],
        }
    local = str(_state.get("local_path") or "")
    return {
        "ok": bool(_state.get("ok")),
        "error": str(_state.get("error") or ""),
        "status": str(_state.get("status") or "idle"),
        "serial": str(_state.get("serial") or ""),
        "package": str(_state.get("package") or ""),
        "activity": str(_state.get("activity") or ""),
        "remote_path": str(_state.get("remote_path") or ""),
        "local_path": local,
        "source": str(_state.get("source") or "capture"),
        "original_name": str(_state.get("original_name") or ""),
        "started_at": str(_state.get("started_at") or ""),
        "finished_at": str(_state.get("finished_at") or ""),
        "size_bytes": int(_state.get("size_bytes") or 0),
        "thread_id": cur or want,
        "id": str(_state.get("id") or (Path(local).name if local else "")),
        "summary": _state.get("summary"),
        "data_path": str(_state.get("data_path") or ""),
        "detaildata_path": str(_state.get("detaildata_path") or ""),
        "aidata_path": str(_state.get("aidata_path") or ""),
        "aidata": _state.get("aidata"),
        "shots": _list_shots(Path(local)) if local else [],
    }


def latest_profiler_file(name: str = "") -> Path | None:
    if name:
        return _resolve_raw_name(name)
    path = str(_state.get("local_path") or "").strip()
    if not path:
        return None
    file = Path(path)
    if file.is_file() and file.stat().st_size > 0:
        return file
    return None


def list_profiler_history(limit: int = 40, thread_id: str = "") -> dict[str, Any]:
    """磁盘历史（新→旧）。必须带 thread_id，只列本会话（与 Perfetto 一致）。"""
    tid = _safe_thread_id(thread_id)
    if not tid:
        return {"ok": True, "error": "", "items": []}

    root = _profiler_dir()
    items: list[dict[str, Any]] = []
    prefix = f"{tid}__"
    try:
        files = sorted(
            (
                p
                for p in root.glob("*.raw")
                if p.name.startswith(prefix) and _parse_raw_name(p.name)
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception as e:
        return {"ok": False, "error": str(e), "items": []}

    for path in files[: max(1, min(100, int(limit or 40)))]:
        parsed = _parse_raw_name(path.name) or {}
        sib = _sibling_paths(path)
        meta: dict[str, Any] = {
            "id": path.name,
            "local_path": str(path),
            "size_bytes": path.stat().st_size,
            "mtime": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
            "serial": str(parsed.get("serial") or ""),
            "package": "",
            "has_summary": False,
            "has_data": sib["data"].is_file(),
            "has_detaildata": sib["detaildata"].is_file(),
            "has_aidata": sib["aidata"].is_file(),
            "ok": False,
            "valid_magic": False,
            "thread_id": tid,
        }
        side = _sidecar_path(path)
        if side.is_file():
            try:
                saved = json.loads(side.read_text(encoding="utf-8"))
                meta["has_summary"] = True
                meta["source"] = str(saved.get("source") or "capture")
                meta["original_name"] = str(saved.get("original_name") or "")
                meta["ok"] = bool(saved.get("ok"))
                meta["serial"] = str(saved.get("serial") or ("" if saved.get("source") == "import" else meta["serial"]))
                meta["package"] = str(saved.get("package") or "")
                summary = saved.get("summary") or {}
                if isinstance(summary, dict):
                    meta["valid_magic"] = bool(summary.get("valid_magic"))
            except Exception:
                pass
        items.append(meta)
    return {"ok": True, "error": "", "items": items}


def load_profiler_history(name: str, thread_id: str = "") -> dict[str, Any]:
    """打开一条历史：优先 sidecar，否则窥视并补写。"""
    raw = _resolve_raw_name(name)
    want = _safe_thread_id(thread_id)
    parsed = _parse_raw_name(raw.name) if raw else None
    if raw is None:
        return {
            **latest_profiler(want),
            "ok": False,
            "error": "找不到该 .raw",
            "status": "error",
        }
    # 有会话键时必须带本会话前缀；无前缀的旧文件也不跨会话打开
    if want and (not parsed or parsed.get("thread_id") != want):
        return {
            **latest_profiler(want),
            "ok": False,
            "error": "该 .raw 不属于当前会话",
            "status": "error",
        }

    side = _sidecar_path(raw)
    if side.is_file():
        try:
            saved = json.loads(side.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                saved["local_path"] = str(raw)
                saved["id"] = raw.name
                saved["status"] = str(saved.get("status") or "done")
                if parsed:
                    saved["thread_id"] = parsed["thread_id"]
                sib = _sibling_paths(raw)
                # recording/pulling/converting 只活在当前进程。磁盘上留下的是中断，
                # 按完成打开，否则按钮一直转、也不能再点「生成分析数据」。
                live = str(saved.get("status") or "")
                same_live = (
                    str(_state.get("id") or "") == raw.name
                    and str(_state.get("status") or "") == live
                    and live in ("recording", "pulling", "converting")
                )
                stale_live = live in ("recording", "pulling", "converting") and not same_live
                if stale_live:
                    saved["status"] = "done"
                    if (
                        live == "converting"
                        and not sib["detaildata"].is_file()
                        and not str(saved.get("error") or "")
                    ):
                        saved["error"] = "上次生成分析数据没有跑完，可再点「生成分析数据」。"
                if sib["data"].is_file():
                    saved["data_path"] = str(sib["data"])
                if sib["detaildata"].is_file():
                    saved["detaildata_path"] = str(sib["detaildata"])
                    # 文件已经在了，磁盘上残留的中断提示就作废
                    if saved.get("error") == "上次生成分析数据没有跑完，可再点「生成分析数据」。":
                        saved["error"] = ""
                        stale_live = True
                if sib["aidata"].is_file():
                    saved["aidata_path"] = str(sib["aidata"])
                    loaded = _load_aidata_file(sib["aidata"])
                    if loaded is not None:
                        saved["aidata"] = loaded
                # 旧 sidecar 无 unity_version 时补窥视，便于「打开 Profiler」匹配 Hub
                summ = saved.get("summary")
                if isinstance(summ, dict) and not summ.get("unity_version"):
                    peek = _peek_raw(raw)
                    summ = {**summ, **{k: peek[k] for k in ("unity_version", "areas", "valid_magic", "size_bytes") if k in peek}}
                    if peek.get("notes"):
                        summ["notes"] = peek["notes"]
                    saved["summary"] = summ
                    _save_sidecar(saved)
                elif stale_live:
                    _save_sidecar(saved)
                _state.clear()
                _state.update(saved)
                return latest_profiler(want or str(saved.get("thread_id") or ""))
        except Exception as e:
            _log.warning("读 Profiler sidecar 失败，将重窥视：%s", e)

    summary = _peek_raw(raw)
    serial = (parsed or {}).get("serial") or ""
    if not serial:
        m = _RAW_LEGACY_RE.match(raw.name)
        serial = m.group("serial") if m else ""
    payload = {
        "ok": bool(summary.get("valid_magic")) or summary["size_bytes"] > 0,
        "error": "",
        "status": "done",
        "serial": serial,
        "package": "",
        "activity": "",
        "remote_path": "",
        "local_path": str(raw),
        "started_at": "",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "size_bytes": int(summary.get("size_bytes") or raw.stat().st_size),
        "thread_id": (parsed or {}).get("thread_id") or want,
        "id": raw.name,
        "summary": summary,
    }
    sib = _sibling_paths(raw)
    if sib["data"].is_file():
        payload["data_path"] = str(sib["data"])
    if sib["detaildata"].is_file():
        payload["detaildata_path"] = str(sib["detaildata"])
    if sib["aidata"].is_file():
        payload["aidata_path"] = str(sib["aidata"])
        loaded = _load_aidata_file(sib["aidata"])
        if loaded is not None:
            payload["aidata"] = loaded
    _state.clear()
    _state.update(payload)
    _save_sidecar(payload)
    return latest_profiler(want or payload["thread_id"])


_PLUGIN_PORT = 17890
_PLUGIN_FOLDER = "KabuProfiler"


def _device_plugin_dir(package: str) -> str:
    return f"/sdcard/Android/data/{package}/files/{_PLUGIN_FOLDER}"


def _shot_dir_for(raw: Path) -> Path:
    return raw.with_name(raw.stem + "_shots")


def _list_shots(raw: Path) -> list[str]:
    folder = _shot_dir_for(raw)
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.glob("*.jpg"))


def _plugin_http(method: str, path: str, timeout: float = 2.5) -> dict[str, Any] | None:
    import urllib.error
    import urllib.request

    data = b"{}" if method == "POST" else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{_PLUGIN_PORT}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body or "{}")
            return parsed if isinstance(parsed, dict) else None
    except urllib.error.HTTPError as e:
        # 4xx 仍可能带插件自己的 error，别当成「没连上」
        try:
            body = e.read().decode("utf-8", errors="replace")
            parsed = json.loads(body or "{}")
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, OSError):
            pass
        _log.info("插件 HTTP %s %s 返回 %s", method, path, e.code)
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        _log.info("插件 HTTP %s %s 不可用：%s", method, path, e)
        return None


def _plugin_reverse(serial: str) -> None:
    _run_adb(
        serial,
        ["reverse", f"tcp:{_PLUGIN_PORT}", f"tcp:{_PLUGIN_PORT}"],
        timeout=8,
    )


def _plugin_status_file(serial: str, package: str) -> dict[str, Any] | None:
    remote = f"{_device_plugin_dir(package)}/status.json"
    code, out, _ = _run_adb(serial, ["shell", "cat", remote], timeout=8)
    text = (out or "").strip()
    if code != 0 or not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _plugin_write_cmd(serial: str, package: str, action: str) -> bool:
    remote_dir = _device_plugin_dir(package)
    # cmd 由游戏主线程吃掉；整段作为 adb shell 的一个参数，避免本机拆引号
    script = f"mkdir -p {remote_dir} && echo {action} > {remote_dir}/cmd"
    code, _, _ = _run_adb(serial, ["shell", script], timeout=8)
    return code == 0


def _plugin_begin(serial: str, package: str) -> tuple[bool, str, str, str]:
    """返回 (ok, raw_path, shot_dir, error)。HTTP 优先，失败再写 cmd。"""
    _plugin_reverse(serial)
    if _plugin_http("GET", "/status", timeout=1.5) is not None:
        started = _plugin_http("POST", "/start", timeout=8)
        if not started or not started.get("ok"):
            err = str((started or {}).get("error") or "插件 HTTP /start 失败")
            return False, "", "", err
        return (
            True,
            str(started.get("raw_path") or ""),
            str(started.get("shot_dir") or ""),
            "",
        )
    if not _plugin_write_cmd(serial, package, "start"):
        return (
            False,
            "",
            "",
            "插件 HTTP 17890 不通，写 cmd 也失败。请先打开带 Kabu Profiler 的 Development 包。",
        )
    # cmd 已经写上，必须等游戏吃掉并确认 recording。写在 return 后面会变成死代码，
    # 函数变成返回 None，接口 500，但真机其实已经开始录了。
    deadline = time.time() + 8
    last_err = ""
    while time.time() < deadline:
        st = _plugin_status_file(serial, package)
        if st and st.get("recording") and st.get("raw_path"):
            return True, str(st.get("raw_path") or ""), str(st.get("shot_dir") or ""), ""
        if st and st.get("error"):
            last_err = str(st.get("error"))
        time.sleep(0.4)
    # 没被游戏吃掉的 start 留在磁盘上，下次进游戏会被当成新命令
    _run_adb(
        serial,
        ["shell", "rm", "-f", f"{_device_plugin_dir(package)}/cmd"],
        timeout=5,
    )
    return (
        False,
        "",
        "",
        last_err or "插件没有开始录制。请确认游戏在前台，且已重新打进 Kabu Profiler。",
    )


def _plugin_end(serial: str, package: str) -> tuple[str, str, str]:
    """返回 (raw_path, shot_dir, error)。路径为空表示没停下来。"""
    _plugin_reverse(serial)
    if _plugin_http("GET", "/status", timeout=1.5) is not None:
        stopped = _plugin_http("POST", "/stop", timeout=8)
        if not stopped:
            return "", "", "插件 HTTP /stop 失败"
        raw = str(stopped.get("raw_path") or "")
        if not raw:
            return "", "", str(stopped.get("error") or "插件没有返回 .raw 路径")
        return raw, str(stopped.get("shot_dir") or ""), ""
    if not _plugin_write_cmd(serial, package, "stop"):
        return "", "", "插件 HTTP 不通，写 stop cmd 失败"
    deadline = time.time() + 8
    last_err = ""
    while time.time() < deadline:
        st = _plugin_status_file(serial, package)
        if st and not st.get("recording") and st.get("raw_path"):
            return str(st.get("raw_path") or ""), str(st.get("shot_dir") or ""), ""
        if st and st.get("error"):
            last_err = str(st.get("error"))
        time.sleep(0.4)
    return "", "", last_err or "停止后没有拿到 .raw 路径"


def _pull_shots(serial: str, remote_dir: str, dest: Path) -> None:
    """把设备上的 {stem}_shots 拉到本机与 .raw 同名的目录。失败不挡 .raw。"""
    remote = (remote_dir or "").strip().rstrip("/")
    if not remote.startswith("/"):
        return
    parent = dest.parent
    code, _, err = _run_adb(serial, ["pull", remote, str(parent)], timeout=120)
    if code != 0:
        _log.info("截图 pull 失败：%s", (err or "")[:200])
        return
    pulled = parent / Path(remote).name
    if not pulled.is_dir():
        return
    if pulled.resolve() == dest.resolve():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for jpg in pulled.glob("*.jpg"):
        target = dest / jpg.name
        if target.exists():
            target.unlink()
        jpg.replace(target)
    try:
        if pulled.exists() and not any(pulled.iterdir()):
            pulled.rmdir()
    except OSError:
        pass


def profiler_detaildata_file(name: str = "", thread_id: str = "") -> Path | None:
    """只返回该会话 .raw 旁边的 .detaildata。正文不进 latest 轮询。"""
    if not (name or "").strip():
        return None
    raw, _err = _resolve_session_raw(name, thread_id)
    if raw is None:
        return None
    path = raw.with_suffix(".detaildata")
    if path.is_file():
        return path
    return None


def profiler_shot_file(name: str, file: str) -> Path | None:
    """只允许该 .raw 旁 `_shots` 目录里的 jpg，拒绝路径穿越。"""
    raw = latest_profiler_file(name)
    if raw is None:
        return None
    safe = Path(file or "").name
    if not safe.lower().endswith(".jpg") or safe != file:
        return None
    path = _shot_dir_for(raw) / safe
    if path.is_file():
        return path
    return None


def _remote_size(serial: str, remote: str) -> int:
    code, out, _ = _run_adb(serial, ["shell", "ls", "-l", remote], timeout=8)
    if code != 0:
        return 0
    parts = (out or "").split()
    for token in parts:
        if token.isdigit() and int(token) > 32:
            return int(token)
    return 0


async def start_profiler_raw(
    serial: str = "",
    package: str = "",
    thread_id: str = "",
) -> dict[str, Any]:
    """通知已打开的 Development 包（kabu-profiler）开始写 .raw，不冷启。"""
    tid = _safe_thread_id(thread_id)
    async with _lock:
        if _state.get("status") in ("recording", "pulling", "converting"):
            return {
                **latest_profiler(tid),
                "ok": False,
                "error": "已在录制或正在从手机拉取",
                "status": str(_state.get("status") or "recording"),
            }

        serial = (serial or get_android_serial() or "").strip()
        package = (package or "").strip()
        _state.update(
            {
                "status": "recording",
                "source": "capture",
                "original_name": "",
                "ok": False,
                "error": "",
                "serial": serial,
                "package": package,
                "activity": "",
                "remote_path": "",
                "local_path": "",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": "",
                "size_bytes": 0,
                "thread_id": tid,
                "id": "",
                "summary": None,
            }
        )

        if not tid:
            _state.update(
                {
                    "status": "error",
                    "error": "缺少会话 thread_id，无法落入本会话历史",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return latest_profiler(tid)

        if not serial:
            _state.update(
                {
                    "status": "error",
                    "error": "未指定设备：请先侧栏连接",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return latest_profiler(tid)

        if not await asyncio.to_thread(_device_online, serial):
            _state.update(
                {
                    "status": "error",
                    "error": f"设备不在线：{serial}",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return latest_profiler(tid)

        if not package:
            try:
                package = await read_foreground(serial)
            except Exception as e:
                _log.warning("读前台失败：%s", e)
                package = ""
            _state["package"] = package

        if not package:
            _state.update(
                {
                    "status": "error",
                    "error": "读不到前台包名：请先打开 Development 游戏再点开始",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return latest_profiler(tid)

        try:
            started, remote, shot_dir, err = await asyncio.to_thread(
                _plugin_begin, serial, package
            )
        except Exception as e:
            # 通知插件失败时不能把状态留在 recording，否则按钮和真机对不上
            _log.warning("通知插件开始失败：%s", e)
            started, remote, shot_dir, err = False, "", "", f"通知插件开始失败：{e}"
        if not started:
            _state.update(
                {
                    "status": "error",
                    "error": err or "插件未开始录制",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return latest_profiler(tid)

        _state.update(
            {
                "status": "recording",
                "ok": True,
                "error": "",
                "remote_path": remote,
                "shot_dir": shot_dir,
            }
        )
        _log.info(
            "插件录制已开 serial=%s package=%s remote=%s",
            serial,
            package,
            remote,
        )
        return latest_profiler(tid)


async def stop_profiler_raw() -> dict[str, Any]:
    """通知插件停录。大文件 pull 放到后台，避免这个请求一直转圈。"""
    global _pull_task
    async with _lock:
        if _state.get("status") not in ("recording", "pulling", "error", "done"):
            if not _state.get("remote_path"):
                return {
                    **latest_profiler(),
                    "ok": False,
                    "error": "当前没有进行中的录制",
                    "status": "idle",
                }

        serial = str(_state.get("serial") or get_android_serial() or "").strip()
        package = str(_state.get("package") or "").strip()
        tid = _safe_thread_id(str(_state.get("thread_id") or ""))

        if not serial or not package:
            _state.update(
                {
                    "status": "error",
                    "ok": False,
                    "error": "缺少设备或包名，无法通知插件停止",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return latest_profiler(tid)

        if _pull_task is not None and not _pull_task.done():
            _state["status"] = "pulling"
            return latest_profiler(tid)

        _state["status"] = "pulling"
        remote, shot_dir, err = await asyncio.to_thread(
            _plugin_end, serial, package
        )
        remote = remote or str(_state.get("remote_path") or "")
        shot_dir = shot_dir or str(_state.get("shot_dir") or "")
        if not remote:
            _state.update(
                {
                    "status": "error",
                    "ok": False,
                    "error": err or "插件没有返回 .raw 路径",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return latest_profiler(tid)

        stamp = time.strftime("%Y%m%d_%H%M%S")
        local = _profiler_dir() / f"{tid}__{serial}_{stamp}.raw"
        _state.update(
            {
                "status": "pulling",
                "ok": True,
                "error": "",
                "remote_path": remote,
                "shot_dir": shot_dir,
                "local_path": str(local),
                "thread_id": tid,
            }
        )
        # 先返回，让按钮结束「通知插件」。大文件 pull 在后台做，面板轮询状态。
        _pull_task = asyncio.create_task(
            _pull_capture(serial, remote, shot_dir, tid, local)
        )
        return latest_profiler(tid)


def _delete_remote_capture(serial: str, remote: str, shot_dir: str) -> None:
    """本机已经有完整 .raw 后再删手机上的原件。一次录制能到数 GB。"""
    raw = (remote or "").strip()
    if raw.startswith("/") and raw.endswith(".raw") and ".." not in raw:
        code, _, err = _run_adb(serial, ["shell", "rm", "-f", raw], timeout=20)
        if code != 0:
            _log.info("删除手机 .raw 失败：%s", (err or "")[:200])
    shots = (shot_dir or "").strip().rstrip("/")
    if shots.startswith("/") and shots.endswith("_shots") and ".." not in shots:
        code, _, err = _run_adb(serial, ["shell", "rm", "-rf", shots], timeout=30)
        if code != 0:
            _log.info("删除手机截图目录失败：%s", (err or "")[:200])


def _pull_timeout_sec(size: int) -> float:
    """按大约 8MB/s 估算。7GB 不能再用固定 180 秒，否则请求先超时、文件只拉到一半。"""
    estimate = size / (8 * 1024 * 1024) + 45
    return max(90.0, min(estimate, 30 * 60))


async def _pull_capture(
    serial: str,
    remote: str,
    shot_dir: str,
    tid: str,
    local: Path,
) -> None:
    """后台 pull。失败时删掉半成品，避免历史里出现打不开的 .raw。"""
    try:
        size = await asyncio.to_thread(_remote_size, serial, remote)
        if size <= 0:
            await asyncio.sleep(0.8)
            size = await asyncio.to_thread(_remote_size, serial, remote)
        if size <= 0:
            async with _lock:
                _state.update(
                    {
                        "status": "error",
                        "ok": False,
                        "error": f"设备上没有 .raw（{remote}）。确认插件已开始写文件。",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "size_bytes": 0,
                    }
                )
            return

        timeout = _pull_timeout_sec(size)
        _log.info("开始 pull .raw size=%s timeout=%.0fs -> %s", size, timeout, local)
        code, out, pull_err = await asyncio.to_thread(
            _run_adb, serial, ["pull", remote, str(local)], timeout=timeout
        )
        if code != 0 or not local.is_file() or local.stat().st_size <= 0:
            local.unlink(missing_ok=True)
            async with _lock:
                _state.update(
                    {
                        "status": "error",
                        "ok": False,
                        "error": f"pull 失败：{(pull_err or out or 'empty').strip()[:400]}",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "local_path": "",
                    }
                )
            return

        if shot_dir:
            await asyncio.to_thread(_pull_shots, serial, shot_dir, _shot_dir_for(local))

        local_size = local.stat().st_size
        if local_size < size:
            # 本机比手机小，说明 pull 不完整，真机上的原件先留着
            _log.warning(
                "pull 不完整 local=%s remote=%s，不删手机上的文件",
                local_size,
                size,
            )
        else:
            await asyncio.to_thread(_delete_remote_capture, serial, remote, shot_dir)

        summary = await asyncio.to_thread(_peek_raw, local)
        async with _lock:
            _state.update(
                {
                    "status": "converting",
                    "ok": True,
                    "error": "",
                    "remote_path": remote,
                    "local_path": str(local),
                    "size_bytes": local.stat().st_size,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "id": local.name,
                    "thread_id": tid,
                    "summary": summary,
                }
            )
            result = latest_profiler(tid)
        _save_sidecar(result)
        _log.info(
            "插件 .raw 已落盘：%s shots=%s，开始生成 .data/.detaildata",
            local,
            len(result.get("shots") or []),
        )
        # 网页不解析 .data。Editor 写 .data + 全帧 .detaildata，回来再裁 .aidata。
        # 全帧扫 marker 比只抽 top 慢，大文件把等待放宽。
        convert_timeout = 1800 if local.stat().st_size > 1024**3 else 900
        try:
            converted = await asyncio.to_thread(
                convert_profiler_analysis, local.name, tid, convert_timeout
            )
        except Exception as e:
            _log.warning("自动生成分析数据失败：%s", e)
            converted = {"ok": False, "error": str(e)}
        async with _lock:
            if str(_state.get("id") or "") == local.name:
                _state["status"] = "done"
                if not converted.get("ok"):
                    _state["error"] = str(
                        converted.get("error") or "生成分析数据失败，可再点「生成分析数据」"
                    )
            result = latest_profiler(tid)
        _save_sidecar(result)
    except Exception as e:
        local.unlink(missing_ok=True)
        _log.warning("pull 异常：%s", e)
        async with _lock:
            _state.update(
                {
                    "status": "error",
                    "ok": False,
                    "error": f"pull 失败：{e}",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "local_path": "",
                }
            )


def profiler_calltree_file(name: str, thread_id: str, frame: int) -> Path | None:
    """按会话定位单帧调用树；不接受客户端提供磁盘路径。"""
    if not name.strip() or not thread_id.strip() or frame < 0:
        return None
    raw, _ = _resolve_session_raw(name, thread_id)
    if raw is None:
        return None
    path = Path(str(raw.with_suffix(".detaildata")) + ".calls") / f"{frame}.json"
    return path if path.is_file() else None
