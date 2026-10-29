"""真机 Perfetto V1：adb 录一段 → pull → Trace Processor 摘要。

不接对话 / 不进 analyze。侧栏或 HTTP 调用即可。同时只允许一趟采集。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.infra.app_logging import get_logger
from server.tools.android_panel import read_foreground
from server.tools.connect import get_android_serial

_log = get_logger("ailearning.perfetto")

# 部分 OEM 禁止 shell 写 /data/local/tmp；优先 stdout 落到本机，不落设备文件
_MIN_SEC = 3
_MAX_SEC = 30
_DEFAULT_SEC = 5

_lock = asyncio.Lock()
_state: dict[str, Any] = {
    "status": "idle",  # idle | running | done | error
    "ok": False,
    "error": "",
    "serial": "",
    "package": "",
    "duration_sec": 0,
    "started_at": "",
    "finished_at": "",
    "trace_path": "",
    "summary": None,
    "thread_id": "",
    "id": "",
}

# 文件名：{thread}__{serial}_{YYYYMMDD}_{HHMMSS}.perfetto-trace
_TRACE_NAME_RE = re.compile(
    r"^(?P<thread>.+?)__(?P<serial>.+)_(?P<date>\d{8})_(?P<time>\d{6})\.perfetto-trace$"
)


def _trace_dir() -> Path:
    root = Path(__file__).resolve().parent.parent / "logs" / "perfetto"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_thread_id(raw: str) -> str:
    """thread_id 进文件名：只留安全字符，避免路径穿越。"""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", (raw or "").strip())
    return (s[:96] if s else "") or ""


def _clamp_duration(sec: float | int | None) -> int:
    try:
        value = int(sec if sec is not None else _DEFAULT_SEC)
    except (TypeError, ValueError):
        value = _DEFAULT_SEC
    return max(_MIN_SEC, min(_MAX_SEC, value))


def _config_text(duration_ms: int) -> str:
    # FrameTimeline + 调度/图形 atrace + GPU 相关：
    # - power/gpu_frequency、gpu_mem：多数机有
    # - gpu.counters / renderstages：有 producer 才有数（华为等常无硬件 counter）
    return f"""\
buffers: {{
  size_kb: 65536
  fill_policy: DISCARD
}}
duration_ms: {duration_ms}
data_sources: {{
  config {{
    name: "linux.ftrace"
    ftrace_config {{
      ftrace_events: "sched/sched_switch"
      ftrace_events: "sched/sched_wakeup"
      ftrace_events: "sched/sched_wakeup_new"
      ftrace_events: "power/suspend_resume"
      ftrace_events: "power/gpu_frequency"
      ftrace_events: "gpu_mem/gpu_mem_total"
      atrace_categories: "gfx"
      atrace_categories: "view"
      atrace_categories: "am"
      atrace_categories: "wm"
      atrace_categories: "sched"
      atrace_categories: "freq"
    }}
  }}
}}
data_sources: {{
  config {{
    name: "android.surfaceflinger.frametimeline"
  }}
}}
data_sources: {{
  config {{
    name: "linux.process_stats"
    process_stats_config {{
      scan_all_processes_on_start: true
    }}
  }}
}}
data_sources: {{
  config {{
    name: "gpu.counters"
    gpu_counter_config {{
      counter_period_ns: 1000000
    }}
  }}
}}
data_sources: {{
  config {{
    name: "gpu.renderstages"
  }}
}}
"""


def _run_adb(serial: str, args: list[str], *, timeout: float, input_text: str | None = None) -> tuple[int, str, str]:
    import subprocess

    cmd = ["adb", "-s", serial, *args]
    try:
        completed = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return 127, "", "未找到 adb"
    except subprocess.TimeoutExpired:
        return 124, "", f"adb 超时：{' '.join(args[:4])}"
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _run_adb_bytes(
    serial: str,
    args: list[str],
    *,
    timeout: float,
    input_bytes: bytes | None = None,
) -> tuple[int, bytes, str]:
    """二进制 stdout（trace）；用 exec-out 避免 shell 改换行。"""
    import subprocess

    cmd = ["adb", "-s", serial, *args]
    try:
        completed = subprocess.run(
            cmd,
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, b"", "未找到 adb"
    except subprocess.TimeoutExpired:
        return 124, b"", f"adb 超时：{' '.join(args[:4])}"
    err = (completed.stderr or b"").decode("utf-8", errors="replace")
    return completed.returncode, completed.stdout or b"", err


def _device_online(serial: str) -> bool:
    code, out, _ = _run_adb(serial, ["get-state"], timeout=5)
    return code == 0 and (out or "").strip() == "device"


def _strip_stdout_log_prefix(data: bytes) -> bytes:
    """`-o -` 时 perfetto_cmd 会把彩色日志打进 stdout，夹在 protobuf 前面。

    不剥掉 Trace Processor 会报 Unknown trace type / corrupt。
    """
    if not data:
        return data
    head = data[: min(8192, len(data))]
    if not (head.startswith(b"\x1b") or b"perfetto_cmd.cc" in head):
        return data
    idx = 0
    n = len(data)
    while idx < n:
        if data[idx] in (10, 13):  # \n \r
            idx += 1
            continue
        # ANSI 或带 perfetto_cmd 的文本行
        line_end = data.find(b"\n", idx)
        chunk = data[idx : line_end if line_end >= 0 else idx + 240]
        if (
            chunk.startswith(b"\x1b")
            or b"perfetto_cmd.cc" in chunk
            or b"Connected to the Perfetto" in chunk
            or b"TTL:" in chunk
        ):
            if line_end < 0:
                return b""
            idx = line_end + 1
            continue
        break
    return data[idx:]


def _write_local_trace(local: Path, data: bytes) -> tuple[bool, str]:
    data = _strip_stdout_log_prefix(data)
    if len(data) < 64:
        return False, f"trace 太短（{len(data)} bytes）"
    # 粗检：剥完仍全是可打印日志则无效
    if data.lstrip().startswith(b"[") and b"perfetto_cmd" in data[:200]:
        return False, "stdout 仍是日志，没有 protobuf"
    if local.exists():
        local.unlink()
    local.write_bytes(data)
    return True, ""


def _pull_remote_trace(serial: str, remote: str, local: Path) -> tuple[bool, str]:
    if local.exists():
        local.unlink()
    code, out, err = _run_adb(serial, ["pull", remote, str(local)], timeout=120)
    _run_adb(serial, ["shell", "rm", "-f", remote], timeout=5)
    if code != 0 or not local.is_file() or local.stat().st_size < 64:
        return False, (err or out or f"pull exit={code}").strip()
    # 少数机 pull 仍可能夹日志；再剥一次无害
    cleaned = _strip_stdout_log_prefix(local.read_bytes())
    if cleaned != local.read_bytes():
        local.write_bytes(cleaned)
    if local.stat().st_size < 64:
        return False, "pull 后 trace 太短"
    return True, ""


def _capture_to_local(serial: str, duration_sec: int, local: Path) -> tuple[bool, str]:
    """录制并落到本机。

    优先写 `/data/misc/perfetto-traces` 再 pull（完整 protobuf）。
    `-o -` 会在 stdout 混彩色日志，Windows 上易得到坏文件，只作后备。
    """
    _run_adb(serial, ["shell", "setprop", "persist.traced.enable", "1"], timeout=5)
    config = _config_text(duration_sec * 1000)
    timeout = float(duration_sec + 45)
    remote = f"/data/misc/perfetto-traces/ailearning_{int(time.time())}.perfetto-trace"
    _run_adb(serial, ["shell", "rm", "-f", remote], timeout=5)

    # 1) 官方目录 + 完整 config
    code, out, err = _run_adb(
        serial,
        ["shell", "perfetto", "--txt", "-c", "-", "-o", remote],
        timeout=timeout,
        input_text=config,
    )
    if code == 0:
        ok, msg = _pull_remote_trace(serial, remote, local)
        if ok:
            return True, ""
        detail_misc = msg
    else:
        detail_misc = (err or out or f"exit={code}").strip()
        _run_adb(serial, ["shell", "rm", "-f", remote], timeout=5)

    # 2) 官方目录 + 短命令
    remote2 = f"/data/misc/perfetto-traces/ailearning_{int(time.time())}_s.perfetto-trace"
    _run_adb(serial, ["shell", "rm", "-f", remote2], timeout=5)
    code2, out2, err2 = _run_adb(
        serial,
        [
            "shell",
            f"perfetto -o {remote2} -t {duration_sec}s sched freq idle am wm gfx view",
        ],
        timeout=timeout,
    )
    if code2 == 0:
        ok2, msg2 = _pull_remote_trace(serial, remote2, local)
        if ok2:
            return True, ""
        detail_short = msg2
    else:
        detail_short = (err2 or out2 or f"exit={code2}").strip()
        _run_adb(serial, ["shell", "rm", "-f", remote2], timeout=5)

    # 3) stdout 后备（剥日志前缀）
    code3, data3, err3 = _run_adb_bytes(
        serial,
        ["exec-out", "perfetto", "--txt", "-c", "-", "-o", "-"],
        timeout=timeout,
        input_bytes=config.encode("utf-8"),
    )
    ok3, msg3 = _write_local_trace(local, data3)
    if code3 == 0 and ok3:
        return True, ""

    return False, (
        f"misc：{detail_misc}；短命令：{detail_short}；"
        f"stdout：{(err3 or msg3 or f'exit={code3}').strip()}"
    )

def _tp_cache_path(file_name: str, sha256: str) -> Path:
    """与官方 perfetto 包同目录命名，避免下次再被 curl 抢下载。"""
    root, ext = os.path.splitext(file_name)
    return (
        Path.home()
        / ".local"
        / "share"
        / "perfetto"
        / "prebuilts"
        / f"{root}-{sha256[:16]}{ext}"
    )


def _ensure_trace_processor_bin() -> Path:
    """用 httpx 拉 Trace Processor（国内 curl→googleapis 常超时 exit 28）。"""
    import hashlib
    import platform
    import sys

    import httpx
    from perfetto.prebuilts.manifests.trace_processor_shell import (
        TRACE_PROCESSOR_SHELL_MANIFEST,
    )

    plat = sys.platform.lower()
    machine = platform.machine().lower()
    entry = None
    for item in TRACE_PROCESSOR_SHELL_MANIFEST:
        if item.get("platform") == plat and machine in (item.get("machine") or []):
            entry = item
            break
    if entry is None:
        raise RuntimeError(f"当前平台无 Trace Processor 预编译包：{plat}-{machine}")

    dest = _tp_cache_path(str(entry["file_name"]), str(entry["sha256"]))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest

    url = str(entry["url"])
    expect = str(entry["sha256"])
    tmp = dest.with_suffix(dest.suffix + f".{os.getpid()}.tmp")
    _log.info("下载 Trace Processor：%s → %s", url, dest)
    try:
        timeout = httpx.Timeout(30.0, read=300.0)
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as resp:
            resp.raise_for_status()
            hasher = hashlib.sha256()
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes(256 * 1024):
                    fh.write(chunk)
                    hasher.update(chunk)
        digest = hasher.hexdigest()
        if digest != expect:
            raise RuntimeError(f"Trace Processor 校验失败：{digest} != {expect}")
        os.replace(tmp, dest)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    return dest


def _query_rows(tp: Any, sql: str) -> list[Any]:
    try:
        return list(tp.query(sql))
    except Exception as e:
        _log.info("perfetto SQL 跳过：%s (%s)", e, sql[:80])
        return []


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


_APP_JANK = {
    "app deadlinemissed",
    "appdeadline missed",
    "appdeadlinemissed",
    "buffer stuffing",
    "bufferstuffing",
}
_SF_JANK_PREFIXES = (
    "surfaceflinger",
    "displayhal",
    "predictionerror",
    "prediction error",
)


def _norm_jank(label: str) -> str:
    return " ".join((label or "").strip().lower().replace("_", " ").split())


def _jank_bucket(jank_type: str) -> str:
    """app | sf | none | other — 官方 FrameTimeline 归因粗分。"""
    key = _norm_jank(jank_type)
    if not key or key in ("none", "none jank"):
        return "none"
    compact = key.replace(" ", "")
    if key in _APP_JANK or compact in {x.replace(" ", "") for x in _APP_JANK}:
        return "app"
    if any(compact.startswith(p.replace(" ", "")) or key.startswith(p) for p in _SF_JANK_PREFIXES):
        return "sf"
    # 文档里 AppDeadlineMissed 等可能带空格/无空格
    if "app" in key and "deadline" in key:
        return "app"
    if "buffer" in key and "stuff" in key:
        return "app"
    if "surfaceflinger" in key or "displayhal" in key:
        return "sf"
    return "other"


def _downsample_series(values: list[float], limit: int = 80) -> list[float]:
    if len(values) <= limit:
        return [round(v, 2) for v in values]
    step = len(values) / limit
    out: list[float] = []
    i = 0.0
    while len(out) < limit and int(i) < len(values):
        out.append(round(values[int(i)], 2))
        i += step
    return out


def _name_matches_package(name: str, package: str) -> bool:
    if not package or not name:
        return False
    return package in name or name in package


def _summarize_trace(trace_path: Path, package: str) -> dict[str, Any]:
    from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

    notes: list[str] = []
    summary: dict[str, Any] = {
        "frame_count": 0,
        "avg_frame_ms": None,
        "p95_frame_ms": None,
        "max_frame_ms": None,
        "fps_est": None,
        "jank_count": None,
        "cpu_top": [],
        "gpu_ms": None,
        "gpu_note": "",
        "gpu_counters_ok": False,
        "gpu_freq_mhz_avg": None,
        "gpu_freq_mhz_min": None,
        "gpu_freq_mhz_max": None,
        "gpu_temp_c_avg": None,
        "gpu_wait_count": 0,
        "gpu_wait_total_ms": None,
        "gpu_wait_avg_ms": None,
        "gpu_wait_max_ms": None,
        "notes": notes,
        # V2
        "scope": "all",
        "frame_source": "",
        "jank_over_16ms": None,
        "jank_typed_count": 0,
        "jank_breakdown": [],
        "blame": {"app": 0, "sf": 0, "none": 0, "other": 0},
        "frame_series_ms": [],
        "cpu_pkg_total_ms": None,
        "cpu_pkg_per_frame_ms": None,
        "cpu_pkg_note": "",
    }

    shell_bin = _ensure_trace_processor_bin()
    tp = TraceProcessor(
        trace=str(trace_path),
        config=TraceProcessorConfig(bin_path=str(shell_bin)),
    )
    try:
        # FrameTimeline 明细（含掉帧类型）；再按前台包过滤
        detail_rows = _query_rows(
            tp,
            """
            SELECT
              actual_frame_timeline_slice.ts AS ts,
              actual_frame_timeline_slice.dur AS dur,
              actual_frame_timeline_slice.jank_type AS jank_type,
              actual_frame_timeline_slice.present_type AS present_type,
              actual_frame_timeline_slice.on_time_finish AS on_time_finish,
              process.name AS pname
            FROM actual_frame_timeline_slice
            LEFT JOIN process USING (upid)
            WHERE actual_frame_timeline_slice.dur > 0
            ORDER BY actual_frame_timeline_slice.ts
            """,
        )
        source = "actual_frame_timeline_slice"
        scoped = list(detail_rows)
        if package and detail_rows:
            pkg_rows = [
                r
                for r in detail_rows
                if _name_matches_package(str(getattr(r, "pname", "") or ""), package)
            ]
            if pkg_rows:
                scoped = pkg_rows
                summary["scope"] = "package"
                notes.append(f"帧统计已限定前台包进程：{package}")
            else:
                summary["scope"] = "all"
                notes.append(
                    f"FrameTimeline 无包名匹配 {package}，改用全进程帧（含 SF/系统 UI）"
                )

        durs_ms: list[float] = []
        series_ms: list[float] = []
        jank_counter: dict[str, int] = {}
        blame = {"app": 0, "sf": 0, "none": 0, "other": 0}

        if scoped and hasattr(scoped[0], "jank_type"):
            for row in scoped:
                dur = float(getattr(row, "dur", 0) or 0)
                if dur <= 0:
                    continue
                ms = dur / 1e6
                durs_ms.append(ms)
                series_ms.append(ms)
                jt = str(getattr(row, "jank_type", "") or "None")
                jank_counter[jt] = jank_counter.get(jt, 0) + 1
                blame[_jank_bucket(jt)] += 1
        else:
            # 无明细列时退回仅 dur
            durs_ms = [
                float(r.dur) / 1e6 for r in scoped if getattr(r, "dur", 0)
            ]
            series_ms = list(durs_ms)

        if not durs_ms:
            choreo = _query_rows(
                tp,
                """
                SELECT dur FROM slice
                WHERE name GLOB 'Choreographer#doFrame*' AND dur > 0
                ORDER BY ts
                """,
            )
            durs_ms = [float(r.dur) / 1e6 for r in choreo if getattr(r, "dur", 0)]
            series_ms = list(durs_ms)
            source = "Choreographer#doFrame"
            if durs_ms:
                notes.append("无 FrameTimeline，改用 Choreographer（偏 UI 线程，无 jank 归因）")

        summary["frame_source"] = source
        if durs_ms:
            sorted_durs = sorted(durs_ms)
            avg = statistics.fmean(durs_ms)
            summary["frame_count"] = len(durs_ms)
            summary["avg_frame_ms"] = round(avg, 2)
            summary["p95_frame_ms"] = round(_percentile(sorted_durs, 0.95) or 0, 2)
            summary["max_frame_ms"] = round(sorted_durs[-1], 2)
            summary["fps_est"] = round(1000.0 / avg, 1) if avg > 0 else None
            over16 = sum(1 for d in durs_ms if d > 16.67)
            summary["jank_over_16ms"] = over16
            # 兼容旧字段：优先「有类型的掉帧」，否则用 >16.7ms
            typed = blame["app"] + blame["sf"] + blame["other"]
            summary["jank_typed_count"] = typed
            summary["jank_count"] = typed if typed else over16
            summary["frame_series_ms"] = _downsample_series(series_ms)
            summary["blame"] = blame
            total_frames = len(durs_ms) or 1
            breakdown = []
            for name, count in sorted(jank_counter.items(), key=lambda x: -x[1]):
                breakdown.append(
                    {
                        "jank_type": name,
                        "count": count,
                        "pct": round(100.0 * count / total_frames, 1),
                        "bucket": _jank_bucket(name),
                    }
                )
            summary["jank_breakdown"] = breakdown[:12]
            notes.append(f"帧来源：{source}")
        else:
            notes.append("未解析到帧数据（机型过旧、录制时无绘制，或权限不足）")

        cpu_rows = _query_rows(
            tp,
            """
            SELECT process.name AS pname, SUM(sched_slice.dur) AS total_dur
            FROM sched_slice
            JOIN thread USING (utid)
            JOIN process USING (upid)
            WHERE process.name IS NOT NULL
            GROUP BY process.name
            ORDER BY total_dur DESC
            LIMIT 8
            """,
        )
        if not cpu_rows:
            cpu_rows = _query_rows(
                tp,
                """
                SELECT process.name AS pname, SUM(slice.dur) AS total_dur
                FROM slice
                JOIN thread_track ON slice.track_id = thread_track.id
                JOIN thread USING (utid)
                JOIN process USING (upid)
                WHERE process.name IS NOT NULL AND slice.dur > 0
                GROUP BY process.name
                ORDER BY total_dur DESC
                LIMIT 8
                """,
            )
            if cpu_rows:
                notes.append("CPU 概况来自 slice（非完整 sched）")

        top = []
        for row in cpu_rows:
            name = str(getattr(row, "pname", "") or "")
            total = float(getattr(row, "total_dur", 0) or 0)
            if not name:
                continue
            top.append({"name": name, "cpu_ms": round(total / 1e6, 1)})
        summary["cpu_top"] = top

        if package:
            hit = next((x for x in top if _name_matches_package(x["name"], package)), None)
            if hit and summary["frame_count"]:
                per = round(hit["cpu_ms"] / summary["frame_count"], 2)
                summary["cpu_pkg_total_ms"] = hit["cpu_ms"]
                summary["cpu_pkg_per_frame_ms"] = per
                summary["cpu_pkg_note"] = (
                    "CPU 每帧粗估 = 该进程录制窗内 CPU 累计 ÷ 帧数；"
                    "不是引擎 Profiler 的脚本/渲染拆分。"
                )
            elif hit:
                summary["cpu_pkg_total_ms"] = hit["cpu_ms"]
                summary["cpu_pkg_note"] = "有 CPU 累计但无帧数，无法粗估每帧。"
            else:
                summary["cpu_pkg_note"] = f"CPU 榜未见包名 {package}"

        # —— GPU：硬件 counter（常无）+ 频率/温度 + waiting for GPU completion ——
        hw_rows = _query_rows(tp, "SELECT COUNT(*) AS c FROM gpu_counter_track")
        hw_n = int(getattr(hw_rows[0], "c", 0) or 0) if hw_rows else 0
        stage_rows = _query_rows(tp, "SELECT COUNT(*) AS c FROM gpu_slice")
        stage_n = int(getattr(stage_rows[0], "c", 0) or 0) if stage_rows else 0
        summary["gpu_counters_ok"] = hw_n > 0 or stage_n > 0

        freq_rows = _query_rows(
            tp,
            """
            SELECT
              MIN(counter.value) / 1e6 AS min_mhz,
              AVG(counter.value) / 1e6 AS avg_mhz,
              MAX(counter.value) / 1e6 AS max_mhz
            FROM counter
            JOIN counter_track ON counter.track_id = counter_track.id
            WHERE counter_track.name GLOB '*gpufreq*'
               OR counter_track.name GLOB '*gpu_freq*'
               OR lower(counter_track.name) = 'gpufrequency'
            """,
        )
        if freq_rows and getattr(freq_rows[0], "avg_mhz", None) is not None:
            summary["gpu_freq_mhz_avg"] = round(float(freq_rows[0].avg_mhz), 1)
            summary["gpu_freq_mhz_min"] = round(float(freq_rows[0].min_mhz), 1)
            summary["gpu_freq_mhz_max"] = round(float(freq_rows[0].max_mhz), 1)

        temp_rows = _query_rows(
            tp,
            """
            SELECT AVG(counter.value) AS avg_raw
            FROM counter
            JOIN counter_track ON counter.track_id = counter_track.id
            WHERE counter_track.name GLOB '*gpu_temp*'
               OR lower(counter_track.name) GLOB '*gputemp*'
            """,
        )
        if temp_rows and getattr(temp_rows[0], "avg_raw", None) is not None:
            raw = float(temp_rows[0].avg_raw)
            # 华为等常为 millidegree（46200→46.2℃）；有的机直接是摄氏
            avg_c = raw / 1000.0 if raw > 200 else raw
            if 0 < avg_c < 120:
                summary["gpu_temp_c_avg"] = round(avg_c, 1)

        wait_rows = _query_rows(
            tp,
            """
            SELECT
              COUNT(*) AS c,
              SUM(dur) / 1e6 AS total_ms,
              AVG(dur) / 1e6 AS avg_ms,
              MAX(dur) / 1e6 AS max_ms
            FROM slice
            WHERE name GLOB 'waiting for GPU completion*' AND dur > 0
            """,
        )
        if wait_rows and int(getattr(wait_rows[0], "c", 0) or 0) > 0:
            summary["gpu_wait_count"] = int(wait_rows[0].c)
            summary["gpu_wait_total_ms"] = round(float(wait_rows[0].total_ms), 2)
            summary["gpu_wait_avg_ms"] = round(float(wait_rows[0].avg_ms), 2)
            summary["gpu_wait_max_ms"] = round(float(wait_rows[0].max_ms), 2)

        if summary["gpu_counters_ok"]:
            summary["gpu_note"] = (
                f"本机有 GPU counter/renderstages（track={hw_n}, slice={stage_n}）；"
                "细项请在 Perfetto UI 的 GPU 组查看。"
            )
        else:
            bits = ["本机无硬件 gpu.counters（已尝试开启，producer 未提供）"]
            if summary["gpu_freq_mhz_avg"] is not None:
                bits.append(
                    f"已采 GPU 频率 ≈ {summary['gpu_freq_mhz_avg']} MHz"
                    f"（{summary['gpu_freq_mhz_min']}–{summary['gpu_freq_mhz_max']}）"
                )
            if summary["gpu_temp_c_avg"] is not None:
                bits.append(f"GPU 温度约 {summary['gpu_temp_c_avg']}°C")
            if summary["gpu_wait_count"]:
                bits.append(
                    f"「等 GPU」{summary['gpu_wait_count']} 次，"
                    f"最长 {summary['gpu_wait_max_ms']} ms"
                )
            bits.append("每帧 GPU ms 需引擎 Profiler；系统侧只能看频率/等待。")
            summary["gpu_note"] = "；".join(bits)
    finally:
        close = getattr(tp, "close", None)
        if callable(close):
            close()

    return summary


def latest_perfetto(thread_id: str = "") -> dict[str, Any]:
    """最近一次采集状态（含摘要）。传 thread_id 时只返回同会话的。"""
    want = _safe_thread_id(thread_id)
    cur = _safe_thread_id(str(_state.get("thread_id") or ""))
    if want and cur and want != cur:
        return {
            "ok": False,
            "error": "",
            "status": "idle",
            "serial": "",
            "package": "",
            "duration_sec": 0,
            "started_at": "",
            "finished_at": "",
            "trace_path": "",
            "summary": None,
            "id": "",
            "thread_id": want,
        }
    trace_name = Path(str(_state.get("trace_path") or "")).name if _state.get("trace_path") else ""
    return {
        "ok": bool(_state.get("ok")),
        "error": str(_state.get("error") or ""),
        "status": str(_state.get("status") or "idle"),
        "serial": str(_state.get("serial") or ""),
        "package": str(_state.get("package") or ""),
        "duration_sec": int(_state.get("duration_sec") or 0),
        "started_at": str(_state.get("started_at") or ""),
        "finished_at": str(_state.get("finished_at") or ""),
        "trace_path": str(_state.get("trace_path") or ""),
        "summary": _state.get("summary"),
        "id": str(_state.get("id") or trace_name),
        "thread_id": cur or want,
    }


def _sidecar_path(trace: Path) -> Path:
    return trace.with_suffix(trace.suffix + ".summary.json")


def _save_sidecar(payload: dict[str, Any]) -> None:
    """把摘要落到 trace 旁，关会话 / 重启服务后仍可从历史打开。"""
    path = str(payload.get("trace_path") or "").strip()
    if not path:
        return
    trace = Path(path)
    if not trace.is_file():
        return
    try:
        data = dict(payload)
        data["id"] = trace.name
        _sidecar_path(trace).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        _log.warning("写 Perfetto sidecar 失败：%s", e)


def _resolve_trace_name(name: str) -> Path | None:
    """只允许 logs/perfetto 下的 basename，防路径穿越。"""
    raw = (name or "").strip().replace("\\", "/")
    base = Path(raw).name
    if not base.endswith(".perfetto-trace"):
        return None
    if base != raw and "/" in raw:
        return None
    file = _trace_dir() / base
    if file.is_file() and file.stat().st_size > 0:
        return file
    return None


def _parse_trace_name(name: str) -> dict[str, str] | None:
    """解析带会话前缀的正式文件名；旧版无 thread 的不认（避免串会话）。"""
    m = _TRACE_NAME_RE.match(name or "")
    if not m:
        return None
    return {
        "thread_id": m.group("thread"),
        "serial": m.group("serial"),
        "date": m.group("date"),
        "time": m.group("time"),
    }


def list_perfetto_history(limit: int = 40, thread_id: str = "") -> dict[str, Any]:
    """磁盘历史（新→旧）。必须带 thread_id，只列本会话。"""
    tid = _safe_thread_id(thread_id)
    if not tid:
        return {"ok": True, "error": "", "items": []}

    root = _trace_dir()
    items: list[dict[str, Any]] = []
    prefix = f"{tid}__"
    try:
        files = sorted(
            (
                p
                for p in root.glob("*.perfetto-trace")
                if p.name.startswith(prefix) and _parse_trace_name(p.name)
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception as e:
        return {"ok": False, "error": str(e), "items": []}

    for path in files[: max(1, min(100, int(limit or 40)))]:
        parsed = _parse_trace_name(path.name) or {}
        meta: dict[str, Any] = {
            "id": path.name,
            "trace_path": str(path),
            "size_bytes": path.stat().st_size,
            "mtime": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
            "serial": str(parsed.get("serial") or ""),
            "package": "",
            "duration_sec": 0,
            "fps_est": None,
            "avg_frame_ms": None,
            "has_summary": False,
            "ok": False,
            "thread_id": tid,
        }
        side = _sidecar_path(path)
        if side.is_file():
            try:
                saved = json.loads(side.read_text(encoding="utf-8"))
                meta["has_summary"] = True
                meta["ok"] = bool(saved.get("ok"))
                meta["serial"] = str(saved.get("serial") or meta["serial"])
                meta["package"] = str(saved.get("package") or "")
                meta["duration_sec"] = int(saved.get("duration_sec") or 0)
                summary = saved.get("summary") or {}
                if isinstance(summary, dict):
                    meta["fps_est"] = summary.get("fps_est")
                    meta["avg_frame_ms"] = summary.get("avg_frame_ms")
            except Exception:
                pass
        items.append(meta)
    return {"ok": True, "error": "", "items": items}


async def load_perfetto_history(name: str, thread_id: str = "") -> dict[str, Any]:
    """打开一条历史：优先 sidecar；没有则现解析并补写。"""
    trace = _resolve_trace_name(name)
    parsed = _parse_trace_name(trace.name) if trace else None
    want = _safe_thread_id(thread_id)
    if trace is None or parsed is None:
        return {
            **latest_perfetto(want),
            "ok": False,
            "error": "找不到该 trace",
            "status": "error",
        }
    # 防打开别的会话文件
    if want and parsed["thread_id"] != want:
        return {
            **latest_perfetto(want),
            "ok": False,
            "error": "该 trace 不属于当前会话",
            "status": "error",
        }

    side = _sidecar_path(trace)
    if side.is_file():
        try:
            saved = json.loads(side.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and saved.get("summary") is not None:
                saved["trace_path"] = str(trace)
                saved["id"] = trace.name
                saved["thread_id"] = parsed["thread_id"]
                saved["status"] = str(saved.get("status") or "done")
                _state.clear()
                _state.update(saved)
                return latest_perfetto(want or parsed["thread_id"])
        except Exception as e:
            _log.warning("读 sidecar 失败，将重解析：%s", e)

    package = ""
    try:
        summary = await asyncio.to_thread(_summarize_trace, trace, package)
        payload = {
            "ok": True,
            "error": "",
            "status": "done",
            "serial": parsed["serial"],
            "package": package,
            "duration_sec": 0,
            "started_at": "",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "trace_path": str(trace),
            "summary": summary,
            "id": trace.name,
            "thread_id": parsed["thread_id"],
        }
        _state.clear()
        _state.update(payload)
        _save_sidecar(payload)
        return latest_perfetto(want or parsed["thread_id"])
    except Exception as e:
        _log.exception("历史 trace 解析失败")
        return {
            "ok": False,
            "error": f"解析失败：{e}",
            "status": "error",
            "serial": parsed["serial"],
            "package": "",
            "duration_sec": 0,
            "started_at": "",
            "finished_at": "",
            "trace_path": str(trace),
            "summary": None,
            "id": trace.name,
            "thread_id": parsed["thread_id"],
        }


def latest_trace_file(name: str = "") -> Path | None:
    """供下载：指定历史名，或最近一次成功落盘的 trace。"""
    if name:
        return _resolve_trace_name(name)
    path = str(_state.get("trace_path") or "").strip()
    if not path:
        return None
    file = Path(path)
    if file.is_file() and file.stat().st_size > 0:
        return file
    return None


async def capture_perfetto(
    serial: str = "",
    duration_sec: int | None = None,
    package: str = "",
    thread_id: str = "",
) -> dict[str, Any]:
    """同步跑完一趟：录 → pull → 摘要。并发第二次会直接拒绝。"""
    tid = _safe_thread_id(thread_id)
    if _lock.locked():
        return {
            **latest_perfetto(tid),
            "ok": False,
            "error": "已有采集在进行",
            "status": "running",
        }

    async with _lock:
        serial = (serial or get_android_serial() or "").strip()
        duration = _clamp_duration(duration_sec)
        package = (package or "").strip()

        _state.update(
            {
                "status": "running",
                "ok": False,
                "error": "",
                "serial": serial,
                "package": package,
                "duration_sec": duration,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": "",
                "trace_path": "",
                "summary": None,
                "thread_id": tid,
                "id": "",
            }
        )

        if not tid:
            _state.update(
                {
                    "status": "error",
                    "error": "缺少会话 thread_id，无法落盘到本会话历史",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return latest_perfetto(tid)

        if not serial:
            _state.update(
                {
                    "status": "error",
                    "error": "未指定设备：请先侧栏连接，或传入 serial",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return latest_perfetto(tid)

        if not await asyncio.to_thread(_device_online, serial):
            _state.update(
                {
                    "status": "error",
                    "error": f"设备不在线：{serial}",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return latest_perfetto(tid)

        if not package:
            try:
                package = await read_foreground(serial)
            except Exception as e:
                _log.warning("读前台失败：%s", e)
                package = ""
            _state["package"] = package

        stamp = time.strftime("%Y%m%d_%H%M%S")
        # 文件名带会话前缀，历史列表按 thread_id 过滤
        fname = f"{tid}__{serial}_{stamp}.perfetto-trace"
        local = _trace_dir() / fname
        _state["id"] = fname

        ok_cap, err_cap = await asyncio.to_thread(
            _capture_to_local, serial, duration, local
        )
        if not ok_cap:
            _state.update(
                {
                    "status": "error",
                    "error": err_cap,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return latest_perfetto(tid)

        _state["trace_path"] = str(local)
        try:
            summary = await asyncio.to_thread(_summarize_trace, local, package)
            _state.update(
                {
                    "status": "done",
                    "ok": True,
                    "error": "",
                    "summary": summary,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception as e:
            _log.exception("解析 trace 失败")
            err = str(e)
            if "curl" in err.lower() or "googleapis" in err.lower() or "exit status 28" in err:
                err = (
                    "解析器下载失败（访问 Google 超时）。"
                    "已改用 httpx 缓存 Trace Processor；请再采一次。"
                    f" 原始错误：{e}"
                )
            _state.update(
                {
                    "status": "error",
                    "ok": False,
                    "error": f"已拉到 trace，但解析失败：{err}",
                    "summary": {
                        "frame_count": 0,
                        "notes": [f"trace 已保存：{local}"],
                    },
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        result = latest_perfetto(tid)
        _save_sidecar(result)
        return result
