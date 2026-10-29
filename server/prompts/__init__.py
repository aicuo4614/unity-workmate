from pathlib import Path

_DIR = Path(__file__).resolve().parent


def load_text(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"缺少 prompt 文件: {path}")
    return path.read_text(encoding="utf-8").strip()


_SCENE_PERF_SYSTEM = load_text(_DIR / "scene_perf.md")
_DEVICE_PERF_SYSTEM = load_text(_DIR / "device_perf.md")
_SCENE_PERF_INTENT = load_text(_DIR / "scene_perf_intent.md")
_RAG_CONTEXT = load_text(_DIR / "rag_context.md")

_SYSTEMS = {
    "scene_perf": _SCENE_PERF_SYSTEM,
    "device_perf": _DEVICE_PERF_SYSTEM,
}


def build_system_for(role: str) -> str:
    text = _SYSTEMS.get(role)
    if not text:
        raise ValueError(f"未知扮演: {role}")
    return text


def format_rag_context(context: str) -> str:
    """把检索片段填进 rag_context.md（{{context}}）。"""
    return _RAG_CONTEXT.replace("{{context}}", (context or "").strip() or "（无命中）")


def intent_hint(role: str) -> str:
    """intent 站扮演专属分流提示（md）；新扮演加 prompts/{role}_intent.md。"""
    if role == "scene_perf":
        return _SCENE_PERF_INTENT
    path = _DIR / f"{role}_intent.md"
    if path.is_file():
        return load_text(path)
    return ""


_PHASE_INSTRUCTIONS: dict[str, dict[str, str]] = {
    "scene_perf": {
        "greet": "开场自我介绍与引导；不调工具。",
        "intent": "",  # 见 intent_hint / scene_perf_intent.md
        "analyze": (
            "你现在在「分析」阶段。具体问题和整场分析都在这里："
            "这台 MCP 的工具已整包绑定，按用户这句话自己挑，采够就停。"
            "证据不足、问当前帧、或用户要全面/重新采时立刻 tool_call，禁止只念旧证据袋；"
            "禁止请示是否允许采数；禁止只信旧证据袋。\n"
            "问「是不是/多少」→ 先结论再依据。"
            "问「怎么办/如何更早/怎么优化」→ 给 2～3 条可执行方案"
            "（对象+字段+目标值+代价），不要「可考虑的方向」软列表；"
            "方案参数可参考本轮检索到的官方文档片段，但与工具实测冲突时以工具为准；"
            "写清方案仍不等于已改。执行站目前不会写入，禁止说批准后就会改。\n"
            "【过绘】若调用了 unity_perf_get_overdraw：用 data.top 自建换行完整 GFM 表，"
            "列：| 排名 | 路径 | 累计降层 | 均层降 | 透明 |；路径完整勿截断。\n"
            "【绘制顺序】须现采 FD list。"
            "`total=0` 且 `rawEventCount>0` = 过滤无命中，去掉过滤看全表。"
            "采够就停工具写口播。"
        ),
        "act": "执行站占位：不调用工具，不改场景。",
    },
    "device_perf": {
        "greet_hint": (
            "你在「开场」：用户尚未提问。用简体中文写 2～4 句口播（不要 JSON）："
            "以「【开场】」开头，后面换行再写正文；"
            "说明你是真机性能助手；若有本局真机上下文，可点名已连机型/前台包；"
            "帧时间还没有；不要调用工具；不要编造设备或数字；不要长篇菜单。"
        ),
        "greet_fallback": (
            "【开场】你好，我是真机性能助手。"
            "左侧会显示已经连上的手机。帧时间还没有，我不会编数字。"
        ),
        "analyze": (
            "你在「分析」。Android MCP 的工具已绑定，按对话自己挑。"
            "看界面用 Snapshot 时 use_vision 保持 false。"
            "前台包优先读本局真机上下文，不要靠 Snapshot ResourceId 猜。"
            "没有帧时间，禁止编造。"
        ),
        "analyze_ask": (
            "按用户的话用简体中文回答。"
            "问有哪些手机时可调用 ListDevices。"
            "问前台应用时：优先用本局真机上下文里的包名直接答；没有再说明未读到。"
            "默认围绕当前前台包；用户另指包名时以用户为准。"
            "工具或上下文没给出的不要编。"
            "没有帧时间工具，禁止编造 FPS 和 CPU/GPU 时间。"
            "不要说你能改 Unity 场景。"
        ),
    },
}


def phase_instruction(role: str, stage: str) -> str:
    if stage == "intent":
        return intent_hint(role) or _PHASE_INSTRUCTIONS.get(role, {}).get("intent", "")
    return _PHASE_INSTRUCTIONS.get(role, {}).get(stage, "")


def greet_hint(role: str) -> str:
    """扮演未写 greet_hint 时用场景优化的开场约束。"""
    return _PHASE_INSTRUCTIONS.get(role, {}).get("greet_hint") or _SCENE_GREET_HINT


def greet_fallback(role: str) -> str:
    return _PHASE_INSTRUCTIONS.get(role, {}).get("greet_fallback") or _SCENE_GREET_FALLBACK


_SCENE_GREET_HINT = """
你在「开场」：用户尚未提问。用简体中文写 2～4 句口播（不要 JSON）：
- 以「【开场】」开头，后面换行再写正文；
- 说明你是场景性能优化助手，可全面采数分析，也可针对具体问题追问；
- 可以回答怎么改；说明目前不会写入场景；
- 句子之间用空行或句号换行，方便阅读；不要调用工具；不要假装已看过场景；不要长篇菜单。
"""

_SCENE_GREET_FALLBACK = (
    "【开场】你好，我是场景性能优化助手。"
    "你可以让我先全面采数分析当前场景，也可以直接问具体问题"
    "（比如谁过绘高、某物体是否先画）。可以问怎么改，目前不会写入场景。"
)
