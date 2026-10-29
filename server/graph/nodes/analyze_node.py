"""分析站。具体问题和整场分析都在这里。

末条是用户句 → 绑定该扮演对应 MCP 的全部工具，由模型按对话挑选（ask 时注入本地 RAG）。
末条不是用户句 → 只根据已有证据袋口播（兼容旧轮次，不再有单独采证站）。
执行站仍不绑工具。
"""
from __future__ import annotations

import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from server import state
from server.tools import tools_for_analyze
from server.evidence.pack import fd_order_brief, summarize_pack
from server.graph.llm import get_model
from server.graph.nodes.run_tools import run_tools
from server.graph.spoken_text import (
    has_tool_markup,
    parse_fake_tool_calls,
    strip_tool_markup,
)
from server.infra.app_logging import get_logger
from server.prompts import build_system_for, format_rag_context, phase_instruction
from server.rag import rag_retriever
from server.roles.defaults import DEFAULT_ROLE
from server.roles.registry.context import build_session_context
from server.roles.registry.report import build_report

phase_name = "analyze"
_log = get_logger("ailearning.analyze")

_SUMMARY_HINT = """
你在「分析·采证汇总」：固定采证刚结束。
根据下方事实骨架，用简体中文写给人看的汇总（不要 JSON、不要 markdown 代码块）：
1. 先一句话说明当前大概卡在哪；
2. 用中文带关键数字（括号可留英文字段）；
3. 点名 2～3 个最值得继续盯的方向；
4. 若有 gaps，用完整句子说明还缺什么；
5. 提醒可继续追问。可以写改法，但不要说批准后就会改场景。
排版（硬性）：用 Markdown；段落之间空一行；列表每项单独一行（`- ` 开头）；
小节用 `## 标题` 独占一行。禁止把整段糊成没有换行的一堵墙。
禁止假装已改场景；禁止开放式「你想做什么」长菜单。
"""

_ASK_HINT = """
你在「分析·对话」：具体问题和整场分析同一条。工具已绑定，按用户这句话自己挑。
用户要全面分析或重新采时，本轮必须 tool_call，禁止只复述旧证据袋。
分析后用简体中文口播（不要 JSON；表格仅在扮演说明允许时使用）。

硬性顺序：需要采数/定位物体时，本轮先 tool_call；禁止先写「直接回答/我先复采」再调工具。
「## 直接回答」只出现在工具跑完后的终轮口播里。

先判断问题类型，再选结构：

【事实题】如「是不是先画 / 谁过绘高 / 现在多少」：
1. ## 直接回答（先给是/否或关键数字）
2. ## 依据（本轮工具数字/事件序）
3. ## 解读（一两句）

【方案题】如「怎么办 / 如何更早画 / 怎么优化 / 有什么改法」：
1. ## 直接回答（能不能、前提一句说清）
2. ## 现状（本轮关键事实，短）
3. ## 方案（按推荐顺序 2～3 条；每条必须可执行）
   - 改哪个对象/组件/材质（有路径更好）
   - 改成什么（具体字段与目标值，如 RenderQueue=1999）
   - 预期收益 + 代价/风险
4. ## 不建议（1～2 条禁区即可）
禁止用「可考虑的方向」这种软标题糊弄；禁止只丢概念不给参数。
只有工具回包说明改过，才能说已经改了。执行站目前不会改场景，禁止说批准后就会改。

共性：
- 关键数字须来自本轮工具或证据袋；工具跑完必须有中文结论；禁止正文伪工具标记。
- 官方文档片段（若有）只补字段/机制/改法；与工具实测冲突时以工具为准。
- 证据不足时立刻 tool_call；禁止请示「是否允许只读采数」。
- 当前帧问题必须复采，禁止只念旧袋。
- 定位物体：用户给了路径/节点名 → 先 `unity_find_game_object`（name 或 path）。
  若只有 Transform、没有 Renderer/可画组件 → **立刻** `unity_list_scene_objects` 展开层级，
  在回报里定位该节点下的子物体，再对子节点 find（可用 path=`父/子`）并对 FD；
  **禁止**请示「是否允许展开子树」，禁止在父节点上结案。
  若 list 回包带「已按已定位节点裁剪子树」，直接读该子树，禁止猜 LOD0/LOD1 等未出现的名字。
- 精确名找不到：换用户词的拼音/近义英文试一次；仍失败则 `unity_list_scene_objects` 扫层级找近似名，
  禁止连打多个精确名后直接「无法定位」。
- Frame Debugger：`total=0` 且 `rawEventCount>0` = 过滤无命中，去掉过滤再 list，禁止同一过滤连打。
- FD 路径/物体名未命中目标子树时：不得直接写「本帧没画」。须先核对全表中 gameObject/path 为空的绘制事件（合批常丢署名），对可疑条目 get_event，并结合子节点 Renderer 是否启用；证据不足只写「尚未归到绘制事件」，不要写成已证实剔除。
- 「直接回答」与「依据」口径必须一致；依据是「尚未归到」时，直接回答禁止写成「没画/已剔除」。
- 目标节点的可绘制子物体还没读到时：禁止给 renderQueue/Sorting 等改法，只写下一步只读动作。
- 采够就停工具写口播；排版用 Markdown，段空行，列表每项一行。
- 小节标题必须单独一行，且标题与正文之间空一行。正确示例：
  ## 直接回答
  （正文……）

  ## 依据
  - 第一条
  禁止写成「##直接回答正文……##依据-场景……」这种粘连长墙。
- 编号方案也必须每条独占一段，禁止「1.…2.…3.…」粘成一行。正确示例：
  1. 改 renderQueue=1999。……
  预期收益：……
  代价：……

  2. 外推相机距离。……
"""


async def _rag_system_message(query: str) -> SystemMessage | None:
    """analyze ask：LLM 英文化检索句 → 向量召回 + 重排；超时/失败则跳过。"""
    q = (query or "").strip()
    if not q or q == "（空提问）":
        return None
    # 首载模型在 CPU 上可能很慢；超时后跳过 RAG，不挡对话
    _rag_timeout_s = 90.0
    try:
        _log.info("rag search start q=%r", q[:120])
        hits, used_q = await asyncio.wait_for(
            rag_retriever.asearch(q, top_k=5, recall_k=20, llm=get_model()),
            timeout=_rag_timeout_s,
        )
        if used_q != q:
            _log.info("rag query rewritten: %r -> %r", q[:120], used_q[:120])
        _log.info("rag search done hits=%s", len(hits or []))
    except asyncio.TimeoutError:
        _log.warning("rag search timeout after %.0fs, skip", _rag_timeout_s)
        return None
    except Exception as e:
        _log.warning("rag search skipped: %s", e)
        return None
    if not hits:
        return None
    parts: list[str] = []
    for h in hits:
        src = h.get("source") or "?"
        text = (h.get("text") or "").strip()
        if not text:
            continue
        score = h.get("score")
        head = f"[来源: {src}]"
        if isinstance(score, (int, float)):
            head += f" score={float(score):.3f}"
        parts.append(f"{head}\n{text}")
    if not parts:
        return None
    return SystemMessage(content=format_rag_context("\n\n---\n\n".join(parts)))


def _evidence_thin(evidence: dict | None) -> bool:
    """尚无有效 capture 等核心证据 → 首轮应强制采数。"""
    entry = (evidence or {}).get("capture")
    if isinstance(entry, dict) and entry.get("raw") is not None:
        return False
    return True


def _ai_raw_text(msg) -> str:
    raw = getattr(msg, "content", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, list):
        parts: list[str] = []
        for block in raw:
            if isinstance(block, str) and block.strip():
                parts.append(block.strip())
            elif isinstance(block, dict):
                t = block.get("text")
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
        return "\n".join(parts).strip()
    return ""


def _ai_text(msg) -> str:
    return strip_tool_markup(_ai_raw_text(msg))


def _last_human(agent_state: state.AgentState) -> str:
    for m in reversed(agent_state.get("messages") or []):
        if isinstance(m, HumanMessage):
            return str(getattr(m, "content", "") or "").strip()
    return ""


def _is_ask(agent_state: state.AgentState) -> bool:
    msgs = agent_state.get("messages") or []
    return bool(msgs) and isinstance(msgs[-1], HumanMessage)


async def analyze_node(agent_state: state.AgentState):
    role = agent_state.get("role") or DEFAULT_ROLE
    if _is_ask(agent_state):
        return await _analyze_ask(agent_state, role)
    return await _analyze_summary(agent_state, role)


async def _analyze_summary(agent_state: state.AgentState, role: str):
    skeleton = build_report(role, agent_state)
    evidence = agent_state.get("evidence") or {}
    pack = summarize_pack(evidence)
    last = agent_state.get("last_observation") or {}
    facts = {
        "report_skeleton": skeleton,
        "last_source": last.get("source"),
        "evidence": pack,
        "evidence_missing": pack.get("missing") or [],
    }
    sys = [SystemMessage(content=build_system_for(role))]
    hint = phase_instruction(role, phase_name)
    if hint:
        sys.append(SystemMessage(content=hint))
    sys.append(SystemMessage(content=_SUMMARY_HINT))
    sys.append(
        SystemMessage(
            content="事实骨架：\n" + json.dumps(facts, ensure_ascii=False, indent=2)
        )
    )
    full = await _astream_full(get_model(), sys)
    text = _ai_text(full) if full is not None else ""
    if not text:
        missing = pack.get("missing") or []
        gap = ("还缺：" + "、".join(missing)) if missing else "证据袋已有部分采集。"
        text = f"【分析】采证已完成。{gap}可继续追问具体问题。"
    return {
        "phase": phase_name,
        "analyze": {"summary": text, "mode": "summary", "last_answer": text},
        "messages": [AIMessage(content=text)],
    }


async def _analyze_ask(agent_state: state.AgentState, role: str):
    prefs = agent_state.get("session_prefs") or {}
    analyze = agent_state.get("analyze") or {}
    evidence = agent_state.get("evidence") or {}
    user_q = _last_human(agent_state) or "（空提问）"
    thin = _evidence_thin(evidence)

    tool_list = tools_for_analyze(role)
    sys = [SystemMessage(content=build_system_for(role))]
    hint = phase_instruction(role, phase_name)
    if hint:
        sys.append(SystemMessage(content=hint))
    ask_hint = phase_instruction(role, "analyze_ask") or _ASK_HINT
    sys.append(SystemMessage(content=ask_hint))
    # 扮演可注入侧栏事实（如真机前台包）；无登记则空串跳过
    session_ctx = await build_session_context(role)
    if session_ctx:
        sys.append(SystemMessage(content=session_ctx))
    if thin and tool_list:
        sys.append(
            SystemMessage(
                content=(
                    "当前证据袋几乎为空：本回合第一件事必须发起 tool_call 采数，"
                    "禁止只输出「回答不了 / 请确认是否采集」。"
                )
            )
        )
    if analyze.get("summary"):
        sys.append(
            SystemMessage(
                content="此前汇总（背景）：\n" + str(analyze.get("summary"))[:1200]
            )
        )
    if analyze.get("last_answer"):
        sys.append(
            SystemMessage(
                content="上一轮结论：\n" + str(analyze.get("last_answer"))[:400]
            )
        )
    sys.append(
        SystemMessage(
            content="证据袋摘要：\n"
            + json.dumps(summarize_pack(evidence), ensure_ascii=False)
        )
    )
    rag_msg = await _rag_system_message(user_q)
    if rag_msg is not None:
        sys.append(rag_msg)
    if prefs:
        sys.append(
            SystemMessage(
                content="session_prefs：\n"
                + json.dumps(prefs, ensure_ascii=False, indent=2)
            )
        )

    messages: list = [HumanMessage(content=user_q)]
    evidence_next = dict(evidence)
    allowed_names = {
        getattr(t, "name", "") for t in (tool_list or []) if getattr(t, "name", None)
    }
    ran_tools = False
    # 绑工具的轮次静音；若该轮直接口头结案，后面强制再流式收口一遍
    need_stream_close = False
    # 空袋零工具时最多再催两回，避免提供商忽略 tool_choice 时死循环
    empty_tool_nudges = 0

    while True:
        # 有工具就绑上，由模型自己决定继续采还是写口播（不设轮次上限）
        if tool_list and thin and not ran_tools:
            try:
                model = get_model().bind_tools(tool_list, tool_choice="required")
            except Exception:
                model = get_model().bind_tools(tool_list)
        elif tool_list:
            model = get_model().bind_tools(tool_list)
        else:
            model = get_model()

        # 可调工具时静音 ainvoke，避免计划旁白夹在 TOOL 之间；结案后再流式收口。
        if tool_list:
            full = await _ainvoke_silent(model, sys + messages)
        else:
            full = await _astream_full(model, sys + messages)
        if full is None:
            break
        raw = _ai_raw_text(full)
        real_tcs = list(getattr(full, "tool_calls", None) or [])
        if not real_tcs and tool_list and has_tool_markup(raw):
            fake = parse_fake_tool_calls(raw, allowed_names=allowed_names)
            if fake:
                full = AIMessage(content=strip_tool_markup(raw) or "", tool_calls=fake)
        messages.append(full)
        if not getattr(full, "tool_calls", None) or not tool_list:
            # 空袋却零工具：再钉一句后重试（防提供商忽略 tool_choice）
            if tool_list and thin and not ran_tools and empty_tool_nudges < 2:
                empty_tool_nudges += 1
                messages.append(
                    SystemMessage(
                        content=(
                            "你还没有调用任何工具。请立即 tool_call："
                            "必要时 enter_play，再用 FrameDebugger / capture / find 回答用户问题。"
                        )
                    )
                )
                continue
            # 正文在静音轮产出：标记收口再流一次
            if tool_list and _ai_text(full):
                need_stream_close = True
            break
        tool_msgs = await run_tools(messages, tool_list, evidence_sink=evidence_next)
        messages.extend(tool_msgs)
        ran_tools = True
        thin = _evidence_thin(evidence_next)
        # 有有效 FD 前段时钉进上下文，避免后续空过滤把结论冲掉
        fd_brief = fd_order_brief(evidence_next)
        if fd_brief and not any(
            isinstance(m, SystemMessage)
            and "Frame Debugger 前段" in str(getattr(m, "content", "") or "")
            for m in messages
        ):
            messages.append(
                SystemMessage(
                    content=fd_brief
                    + "\n（若某次 list total=0 且 rawEventCount>0，那是过滤无命中，不要再打同一过滤。）"
                )
            )

    text = ""
    need_close = False
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            raw = _ai_raw_text(m)
            if has_tool_markup(raw) and not parse_fake_tool_calls(
                raw, allowed_names=allowed_names or None
            ):
                need_close = True
            text = strip_tool_markup(raw)
            if text and not need_close:
                break
            if need_close:
                break

    last = messages[-1] if messages else None
    last_still_tools = bool(
        isinstance(last, AIMessage) and getattr(last, "tool_calls", None)
    )
    fd_brief = fd_order_brief(evidence_next)
    # 跑过工具却无净口播 / 最后仍停在 tool_calls → 强制收口（不绑工具）
    # 有 FD 前段时也再收一版：把顺序表显式塞进收口，对齐「先序后结论」的写法
    should_close = need_stream_close or (
        ran_tools
        and (
            not text
            or need_close
            or last_still_tools
            or (fd_brief and len(text) < 80)
        )
    )
    if should_close:
        close_bits = [
            "工具已执行完毕。请用简体中文直接回答用户原问题；",
            "不要再调工具；禁止伪工具标记；按 Markdown 小节排版。",
            "每个 ## 标题独占一行，下一行才是正文；列表每项单独一行。",
            "结构：事实题「直接回答→依据→解读」；"
            "方案题「直接回答→现状→方案1/2/3（含字段与目标值）→不建议」。",
            "「直接回答」与「依据」口径必须一致：依据写「尚未归到」时，直接回答禁止写成「没画/已剔除」。",
            "目标节点子树（含 Renderer 的子物体）还没读全时：禁止给 renderQueue/Sorting 等改法，"
            "只写还需对哪个子节点 find/对照 FD。",
            "若 FD list 曾出现 total=0 但 rawEventCount>0，按「过滤无命中」处理，勿声称工具坏了。",
            "过绘排名高 ≠ 最先绘制；事件名里没有物体也不等于没画，要结合全表顺序说明。",
            "若只查到父节点且无 Renderer：必须已用 list 看过该节点子树；否则依据里写明「未展开子树则不能断定未绘制」。",
            "若 FD 无路径命中但存在空 gameObject/path 的绘制事件：不得写成已证实整棵子树未提交。",
        ]
        if fd_brief:
            close_bits.append("下列前段顺序必须用于依据：\n" + fd_brief)
        try:
            close = await _astream_full(
                get_model(),
                sys
                + [SystemMessage(content="\n".join(close_bits))]
                + messages,
            )
            closed = _ai_text(close) if close is not None else ""
            if closed:
                text = closed
                messages.append(
                    AIMessage(content=closed)
                    if not isinstance(close, AIMessage)
                    else close
                )
            else:
                _log.warning("analyze 收口调用无正文")
        except Exception as e:
            _log.warning("analyze 收口失败: %s", e)

    if not text:
        text = (
            "本轮采到了部分工具结果，但未能整理出口播。"
            "请再问一次，或说明物体大致路径（例如含 shan 的节点）。"
        )

    new_messages = messages[1:]
    if not new_messages:
        new_messages = [AIMessage(content=text)]
    elif isinstance(new_messages[-1], AIMessage) and not _ai_text(new_messages[-1]):
        new_messages[-1] = AIMessage(content=text)
    elif not isinstance(new_messages[-1], AIMessage):
        new_messages.append(AIMessage(content=text))

    last_obs = None
    for kind, entry in evidence_next.items():
        if not isinstance(entry, dict) or entry.get("raw") is None:
            continue
        prev = (evidence or {}).get(kind) if isinstance(evidence, dict) else None
        if isinstance(prev, dict) and prev.get("raw") is entry.get("raw"):
            continue
        last_obs = {"source": entry.get("source") or kind, "raw": entry.get("raw")}

    update: dict = {
        "phase": phase_name,
        "analyze": {
            "summary": analyze.get("summary") or text,
            "mode": "ask",
            "last_answer": text,
        },
        "messages": new_messages,
        "evidence": evidence_next,
    }
    if last_obs:
        update["last_observation"] = last_obs
    return update


async def _ainvoke_silent(model, messages):
    """工具轮：ainvoke 并打 nostream_ui，stream_bridge 不把中间正文推给 client。"""
    return await model.ainvoke(
        messages,
        config={
            "tags": ["analyze_tool_round", "nostream_ui"],
            "run_name": "analyze_tools",
        },
    )


async def _astream_full(model, messages):
    """口播轮：astream，供 stream_bridge 逐字推 TOKEN。"""
    full = None
    async for chunk in model.astream(messages):
        full = chunk if full is None else full + chunk
    return full
