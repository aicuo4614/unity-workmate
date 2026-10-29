"""本局证据袋：按 kind 叠加；同 kind 后写覆盖。

last_observation 表示「最近一次任意工具」；分析汇总应读本袋摘要，
不要只啃单槽 last_observation。无 baseline / 成功口径字段。
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from server.evidence.payload import unwrap_tool_payload

# MCP 工具名 → evidence kind（一对一；未列出的工具不进袋）
TOOL_TO_KIND: dict[str, str] = {
    "unity_perf_capture_frame": "capture",
    "unity_perf_renderer_sweep": "renderer_sweep",
    "unity_perf_get_overdraw": "overdraw",
    "unity_get_occlusion_info": "occlusion",
    "unity_get_post_process_stack": "post_process",
    "unity_get_render_census": "render_census",
    "unity_frame_debugger_list": "frame_debugger",
}

KIND_ORDER = (
    "capture",
    "frame_debugger",
    "renderer_sweep",
    "overdraw",
    "occlusion",
    "post_process",
    "render_census",
)


def merge_evidence(old: dict[str, Any] | None, patch: dict[str, Any] | None) -> dict[str, Any]:
    """浅合并 kind；patch 里的同 kind 整项覆盖。"""
    out = dict(old or {})
    if not patch:
        return out
    for k, v in patch.items():
        if v is None:
            continue
        out[str(k)] = v
    return out


def entry_from_tool(tool_name: str, raw: Any) -> dict[str, Any] | None:
    kind = TOOL_TO_KIND.get(tool_name or "")
    if not kind:
        return None
    parsed = unwrap_tool_payload(raw)
    return {
        "kind": kind,
        "source": tool_name,
        "raw": raw,
        "summary": _summarize(kind, parsed if isinstance(parsed, dict) else {}),
    }


def collect_from_messages(
    existing: dict[str, Any] | None,
    messages: list | None,
) -> dict[str, Any]:
    """扫 ToolMessage，按出现顺序写入（后者覆盖同 kind）。"""
    out = dict(existing or {})
    for m in messages or []:
        if not isinstance(m, ToolMessage):
            continue
        name = getattr(m, "name", "") or ""
        entry = entry_from_tool(name, getattr(m, "content", None))
        if entry:
            out[entry["kind"]] = entry
    return out


def missing_kinds(evidence: dict[str, Any] | None) -> list[str]:
    ev = evidence or {}
    return [k for k in KIND_ORDER if k not in ev]


def summarize_pack(evidence: dict[str, Any] | None) -> dict[str, Any]:
    """给澄清/诊断的紧凑事实（不含巨型 raw）。"""
    ev = evidence or {}
    kinds = {k: (ev[k].get("summary") if isinstance(ev.get(k), dict) else None) for k in KIND_ORDER if k in ev}
    return {
        "present": [k for k in KIND_ORDER if k in ev],
        "missing": missing_kinds(ev),
        "kinds": kinds,
    }


def capture_payload(evidence: dict[str, Any] | None) -> dict:
    """从证据袋取 capture 解析体；没有则 {}。"""
    entry = (evidence or {}).get("capture")
    if not isinstance(entry, dict):
        return {}
    parsed = unwrap_tool_payload(entry.get("raw"))
    return parsed if isinstance(parsed, dict) else {}


def report_evidence_list(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    """写入 report['evidence'] 的短列表。"""
    rows: list[dict[str, Any]] = []
    for kind in KIND_ORDER:
        entry = (evidence or {}).get(kind)
        if not isinstance(entry, dict):
            continue
        summary = entry.get("summary") or {}
        note = summary.get("note") or _fallback_note(kind, summary)
        rows.append(
            {
                "kind": kind,
                "source": entry.get("source") or "",
                "note": note,
            }
        )
    return rows


def _fallback_note(kind: str, summary: dict) -> str:
    if not summary:
        return f"{kind}: 已采集"
    parts = [f"{k}={v}" for k, v in list(summary.items())[:6] if k != "note" and v is not None]
    return "; ".join(parts) if parts else f"{kind}: 已采集"


def _summarize(kind: str, parsed: dict) -> dict[str, Any]:
    if kind == "capture":
        return _sum_capture(parsed)
    if kind == "renderer_sweep":
        return _sum_sweep(parsed)
    if kind == "overdraw":
        return _sum_overdraw(parsed)
    if kind == "occlusion":
        return _sum_occlusion(parsed)
    if kind == "post_process":
        return _sum_post(parsed)
    if kind == "render_census":
        return _sum_census(parsed)
    if kind == "frame_debugger":
        return _sum_fd_list(parsed)
    return {"note": f"{kind} 已采集"}


def _sum_fd_list(parsed: dict) -> dict[str, Any]:
    """保留前段绘制顺序，供 analyze 收口；过滤空结果单独标记。"""
    raw_n = int(parsed.get("rawEventCount") or 0)
    total = int(parsed.get("total") or 0)
    returned = int(parsed.get("returned") or 0)
    filter_miss = total == 0 and raw_n > 0
    events = parsed.get("events") if isinstance(parsed.get("events"), list) else []
    early: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        idx = ev.get("index")
        if not isinstance(idx, int):
            continue
        early.append(
            {
                "index": idx,
                "type": ev.get("type"),
                "gameObject": ev.get("gameObject") or "",
                "path": (ev.get("path") or "")[:80],
                "name": (ev.get("name") or "")[:50],
            }
        )
    early.sort(key=lambda e: e["index"])
    early = early[:40]
    return {
        "rawEventCount": raw_n,
        "total": total,
        "returned": returned,
        "filterMiss": filter_miss,
        "early": early,
        "note": (
            f"FD filterMiss raw={raw_n}"
            if filter_miss
            else f"FD events={raw_n or total} early={len(early)}"
        ),
    }


def _sum_capture(parsed: dict) -> dict[str, Any]:
    basics = parsed.get("basics") or {}
    data = basics.get("data") or {}
    ctx = parsed.get("context") or {}
    fd = ((parsed.get("frameDebugger") or {}).get("data") or {})
    profile = ((parsed.get("profile") or {}).get("data") or {})
    timing = profile.get("frameTiming") if isinstance(profile.get("frameTiming"), dict) else {}
    hier = profile.get("hierarchyTop") if isinstance(profile.get("hierarchyTop"), list) else []
    hier_heads = []
    for h in hier[:5]:
        if isinstance(h, dict) and h.get("name"):
            hier_heads.append(
                {"name": h.get("name"), "selfMs": h.get("selfMs"), "calls": h.get("calls")}
            )
    return {
        "ok": basics.get("ok"),
        "batches": data.get("batches"),
        "drawCalls": data.get("drawCalls"),
        "setPassCalls": data.get("setPassCalls"),
        "triangles": data.get("triangles"),
        "instancedBatches": data.get("instancedBatches"),
        "scenePath": ctx.get("scenePath"),
        "playMode": ctx.get("playMode"),
        "cpuFrameTimeMs": timing.get("cpuFrameTimeMs"),
        "gpuFrameTimeMs": timing.get("gpuFrameTimeMs"),
        "fdEventCount": fd.get("eventCount"),
        "fdSummaryByType": fd.get("summaryByType"),
        # 有 topEvents 时保留前若干条绘制顺序（含 path），供「谁先画」类追问；
        # 感知默认 fdEventLimit=0 时此处为空，不涨 token。
        "fdEarlyDraws": _fd_early_draws(fd.get("topEvents")),
        "hierarchyTop": hier_heads,
        "note": (
            f"basics.ok={basics.get('ok')}; batches={data.get('batches')}; "
            f"drawCalls={data.get('drawCalls')}; tris={data.get('triangles')}"
        ),
    }


def _fd_early_draws(top_events: Any, limit: int = 24) -> list[dict[str, Any]]:
    if not isinstance(top_events, list):
        return []
    out: list[dict[str, Any]] = []
    for ev in top_events[:limit]:
        if not isinstance(ev, dict):
            continue
        out.append(
            {
                "index": ev.get("index"),
                "type": ev.get("type"),
                "gameObject": ev.get("gameObject"),
                "path": ev.get("path"),
                "name": (ev.get("name") or "")[:80],
            }
        )
    return out


def _sum_sweep(parsed: dict) -> dict[str, Any]:
    # 兼容若干回包形状：top / rows / results
    rows = parsed.get("top") or parsed.get("rows") or parsed.get("results") or []
    if not isinstance(rows, list):
        rows = []
    top_rows: list[dict[str, Any]] = []
    for r in rows[:8]:
        if not isinstance(r, dict):
            continue
        delta = r.get("delta") if isinstance(r.get("delta"), dict) else {}
        drop = delta.get("batchesDrop")
        if drop is None:
            drop = r.get("batchesDrop")
        top_rows.append(
            {
                "path": r.get("path") or r.get("group") or r.get("label") or "",
                "batchesDrop": drop,
            }
        )
    return {
        "top": top_rows,
        "rowCount": len(rows),
        "ok": parsed.get("ok"),
        "note": (
            f"sweep rows={len(rows)}; top="
            + ", ".join(
                f"{t.get('path')}(Δ{t.get('batchesDrop')})"
                for t in top_rows[:5]
                if t.get("path")
            )
        ),
    }


def _sum_overdraw(parsed: dict) -> dict[str, Any]:
    data = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
    pixel = data.get("pixelSample") if isinstance(data.get("pixelSample"), dict) else {}
    top = data.get("top") if isinstance(data.get("top"), list) else []
    heads: list[dict[str, Any]] = []
    for r in top[:8]:
        if not isinstance(r, dict):
            continue
        heads.append(
            {
                "path": r.get("path"),
                "sumLayersDrop": r.get("sumLayersDrop"),
                "avgLayersDrop": r.get("avgLayersDrop"),
                "transparentSlots": r.get("transparentSlots"),
                "likelyTransparent": r.get("likelyTransparent"),
            }
        )
    return {
        "mode": parsed.get("mode") or data.get("scoreKind"),
        "avgOverdraw": pixel.get("avgOverdraw"),
        "maxOverdraw": pixel.get("maxOverdraw"),
        "topCount": len(top),
        "top": heads,
        "note": (
            f"overdraw mode={parsed.get('mode')}; "
            f"avg={pixel.get('avgOverdraw')}; topN={len(top)}"
        ),
    }


def _sum_occlusion(parsed: dict) -> dict[str, Any]:
    return {
        "conclusion": parsed.get("conclusion"),
        "gaps": parsed.get("gaps"),
        "note": f"occlusion conclusion={parsed.get('conclusion')}",
    }


def _sum_post(parsed: dict) -> dict[str, Any]:
    counts = parsed.get("typeCounts") if isinstance(parsed.get("typeCounts"), dict) else {}
    keys = list(counts.keys())[:8]
    return {
        "types": keys,
        "itemCount": parsed.get("count") or parsed.get("total") or len(parsed.get("items") or []),
        "note": f"post_process types={','.join(keys) or 'none'}",
    }


def _sum_census(parsed: dict) -> dict[str, Any]:
    lights = parsed.get("lights") if isinstance(parsed.get("lights"), dict) else {}
    renderers = parsed.get("renderers") if isinstance(parsed.get("renderers"), dict) else {}
    return {
        "lights": lights.get("total") or lights.get("count"),
        "renderers": renderers.get("total") or renderers.get("enabled") or renderers.get("count"),
        "note": (
            f"census lights={lights.get('total') or lights.get('count')}; "
            f"renderers={renderers.get('total') or renderers.get('enabled')}"
        ),
    }


# ReAct 下一轮只喂摘要，避免工具 JSON 三角膨胀；全文进 evidence；开发态时间线见 server/logs。
_COMPACT_MAX_CHARS = 2500
_HIERARCHY_TOOLS = frozenset(
    {
        "unity_list_scene_objects",
        "unity_get_scene_data",
    }
)
_HIERARCHY_MAX_CHARS = 8000


def _unique_keep(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        s = (x or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def focus_names_from_messages(messages: list | None) -> list[str]:
    """从最近 find 回包 / 本轮 tool_call 参数收集要保留的层级锚点（名或路径末段）。"""
    import re

    found: list[str] = []
    for m in messages or []:
        name = getattr(m, "name", None) or ""
        if isinstance(m, ToolMessage) and name == "unity_find_game_object":
            content = str(getattr(m, "content", "") or "")
            for mm in re.finditer(r"名称[：:]\s*(\S+)", content):
                found.append(mm.group(1))
            for mm in re.finditer(r"路径[：:]\s*(\S+)", content):
                found.append(mm.group(1))
    # 同批 find+list：list 压缩时 find 可能尚未落成 ToolMessage，读 AIMessage.args
    for m in reversed(messages or []):
        if not isinstance(m, AIMessage):
            continue
        for tc in getattr(m, "tool_calls", None) or []:
            if not isinstance(tc, dict):
                continue
            args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
            if args.get("name"):
                found.append(str(args["name"]))
            if args.get("path"):
                found.append(str(args["path"]))
        break
    expanded: list[str] = []
    for x in found:
        expanded.append(x)
        if "/" in x:
            expanded.append(x.rsplit("/", 1)[-1])
    return _unique_keep(expanded)


def _extract_indented_subtree(text: str, root_name: str) -> str | None:
    """从 list_scene_objects 缩进文本中抽出某个节点及其后代。"""
    import re

    if not root_name or not text:
        return None
    lines = text.splitlines()
    pat = re.compile(rf"^(\s*)-\s+{re.escape(root_name)}(?:\s|$)")
    start = None
    base_indent = 0
    for i, line in enumerate(lines):
        m = pat.match(line)
        if m:
            start = i
            base_indent = len(m.group(1))
            break
    if start is None:
        return None
    out = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.strip():
            out.append(line)
            continue
        lead = len(line) - len(line.lstrip(" "))
        if lead <= base_indent and line.lstrip().startswith("-"):
            break
        out.append(line)
    return "\n".join(out).rstrip()


def _compact_hierarchy_text(text: str, focus_names: list[str] | None) -> str:
    """层级全文太大：优先裁成已定位节点的子树，避免截断后丢目标分支。"""
    text = text or ""
    header = text.splitlines()[0] if text.strip() else ""
    chunks: list[str] = []
    seen_leaves: set[str] = set()
    for name in focus_names or []:
        leaf = name.rsplit("/", 1)[-1]
        if not leaf or leaf in seen_leaves:
            continue
        sub = _extract_indented_subtree(text, leaf)
        if not sub:
            continue
        seen_leaves.add(leaf)
        chunks.append(f"[subtree:{leaf}]\n{sub}")
    if chunks:
        body = "\n\n".join(chunks)
        result = (
            f"{header}\n"
            f"（已按已定位节点裁剪子树；原文字节约 {len(text)} chars）\n\n"
            f"{body}"
        )
        if len(result) <= _HIERARCHY_MAX_CHARS:
            return result
        return result[:_HIERARCHY_MAX_CHARS] + f"…(truncated,{len(result)} chars)"
    if len(text) <= _HIERARCHY_MAX_CHARS:
        return text
    return (
        text[:_HIERARCHY_MAX_CHARS]
        + f"\n…(truncated,{len(text)} chars；请先 find 目标节点名/路径，再 list，"
        "回包会优先保留该节点子树)"
    )


def _compact_fd_list(parsed: dict) -> dict[str, Any]:
    """list 回包只留 index/type/path/gameObject；过滤空命中要写清，避免模型当工具坏了。"""
    raw_n = int(parsed.get("rawEventCount") or 0)
    total = int(parsed.get("total") or 0)
    returned = int(parsed.get("returned") or 0)
    filter_miss = total == 0 and raw_n > 0
    events = parsed.get("events") if isinstance(parsed.get("events"), list) else []
    # 优先低 index（绘制前段）；最多 40 条
    keyed: list[tuple[int, dict]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        idx = ev.get("index")
        if not isinstance(idx, int):
            continue
        keyed.append(
            (
                idx,
                {
                    "index": idx,
                    "type": ev.get("type"),
                    "gameObject": ev.get("gameObject"),
                    "path": ev.get("path"),
                    "name": (ev.get("name") or "")[:60],
                },
            )
        )
    keyed.sort(key=lambda x: x[0])
    lean = [e for _, e in keyed[:40]]
    out: dict[str, Any] = {
        "_compact": True,
        "tool": "unity_frame_debugger_list",
        "rawEventCount": raw_n,
        "total": total,
        "returned": returned or len(lean),
        "offset": parsed.get("offset"),
        "message": parsed.get("message"),
        "events": lean,
    }
    if filter_miss:
        out["hint"] = (
            "过滤无命中（total=0 但 rawEventCount>0）。"
            "请去掉 nameContains/eventType 再 list，禁止重复同一过滤。"
        )
    return out


def fd_order_brief(evidence: dict[str, Any] | None) -> str:
    """从证据袋抽出 FD 前段顺序，给收口 LLM 用。"""
    entry = (evidence or {}).get("frame_debugger")
    if not isinstance(entry, dict):
        return ""
    summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
    early = summary.get("early") if isinstance(summary.get("early"), list) else []
    if not early:
        return ""
    lines = [
        f"Frame Debugger 前段（rawEventCount={summary.get('rawEventCount')}）："
    ]
    for ev in early[:36]:
        if not isinstance(ev, dict):
            continue
        go = ev.get("gameObject") or ""
        path = ev.get("path") or ""
        label = go or path or (ev.get("name") or "")
        lines.append(
            f"- #{ev.get('index')} {ev.get('type') or ''} {label}".rstrip()
        )
    return "\n".join(lines)


def compact_tool_content_for_llm(
    tool_name: str,
    raw: Any,
    *,
    focus_names: list[str] | None = None,
) -> str:
    """生成给模型看的短回包；未知工具则截断原文。

    层级工具：若有已定位节点名，优先裁成该节点子树，避免 2500 字硬截断砍掉目标分支。
    """
    import json

    name = tool_name or ""
    parsed = unwrap_tool_payload(raw)

    # capture：摘要已含 fdEarlyDraws（有 topEvents 时）；勿把整份 profile 塞回 messages
    entry = entry_from_tool(name, raw)
    if entry:
        payload = {
            "_compact": True,
            "tool": name,
            "kind": entry["kind"],
            "summary": entry["summary"],
        }
        return json.dumps(payload, ensure_ascii=False)

    if name == "unity_frame_debugger_list" and isinstance(parsed, dict):
        return json.dumps(_compact_fd_list(parsed), ensure_ascii=False)

    if isinstance(parsed, (dict, list)):
        text = json.dumps(parsed, ensure_ascii=False)
    else:
        text = str(raw if raw is not None else "")

    if name in _HIERARCHY_TOOLS:
        return _compact_hierarchy_text(text, focus_names)

    if len(text) <= _COMPACT_MAX_CHARS:
        return text
    return text[:_COMPACT_MAX_CHARS] + f"…(truncated,{len(text)} chars)"


def absorb_tool_into_evidence(
    evidence: dict[str, Any] | None,
    tool_name: str,
    raw: Any,
) -> dict[str, Any]:
    """把完整工具回包写入证据袋（同 kind 覆盖）；原地更新并返回同一 dict。

    FD list：过滤无命中（total=0 且 rawEventCount>0）不覆盖已有有效前段顺序。
    """
    out = evidence if evidence is not None else {}
    entry = entry_from_tool(tool_name, raw)
    if not entry:
        return out
    kind = entry["kind"]
    if kind == "frame_debugger":
        summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
        if summary.get("filterMiss"):
            # 空过滤：只记提示，不动已有 early
            if "frame_debugger" not in out:
                out[kind] = entry
            return out
        old = out.get("frame_debugger")
        old_sum = (
            old.get("summary")
            if isinstance(old, dict) and isinstance(old.get("summary"), dict)
            else {}
        )
        old_early = old_sum.get("early") if isinstance(old_sum.get("early"), list) else []
        new_early = summary.get("early") if isinstance(summary.get("early"), list) else []
        # 已有更长的前段顺序时保留（避免小分页覆盖大全表）
        if len(old_early) >= 12 and len(new_early) < len(old_early):
            return out
    out[kind] = entry
    return out
