"""从本地目录加载文档：支持 md/txt/html；可按路径关键词白名单过滤。"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "nav", "header", "footer"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "nav", "header", "footer") and self._skip:
            self._skip -= 1
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        t = data.strip()
        if t:
            self._parts.append(t)

    def text(self) -> str:
        raw = " ".join(self._parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def html_to_text(html: str) -> str:
    p = _HTMLTextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        # 破损 HTML：退回粗剥标签
        return re.sub(r"<[^>]+>", " ", html)
    return p.text()


# ScriptReference 路径关键词（Manual 整本另开规则，见 _path_allowed）。
# 覆盖渲染/性能之外的常用域：物理、动画、UI、音频、资源、编辑器等。
DEFAULT_PATH_ALLOW = (
    # 渲染 / URP / 性能
    "urp",
    "universal-render",
    "universalrender",
    "render-pipeline",
    "renderpipeline",
    "srp",
    "batch",
    "draw-call",
    "drawcall",
    "setpass",
    "lighting",
    "light",
    "shadow",
    "profiler",
    "frame-debugger",
    "framedebugger",
    "overdraw",
    "occlusion",
    "performance",
    "optimiz",
    "gpu-instancing",
    "gpuinstancing",
    "lod",
    "culling",
    "shader",
    "material",
    "texture",
    "mesh",
    "renderer",
    "camera",
    "post-process",
    "postprocess",
    "quality",
    "mobile",
    "queue",
    "renderqueue",
    "passtag",
    "pass-tag",
    "sl-pass",
    "sl-blend",
    "sl-cull",
    "sorting",
    "transparent",
    "graphics",
    "blend",
    "stencil",
    "ztest",
    "zwrite",
    # 场景 / 对象
    "gameobject",
    "transform",
    "prefab",
    "component",
    "monobehaviour",
    "scriptableobject",
    # 物理 / 导航
    "physics",
    "collider",
    "rigidbody",
    "raycast",
    "navmesh",
    "navmeshagent",
    # 动画 / 时间轴
    "animation",
    "animator",
    "timeline",
    "playable",
    # UI / 2D
    "canvas",
    "ugui",
    "uikit",
    "recttransform",
    "sprite",
    "tilemap",
    "2d",
    # 音频 / 粒子
    "audio",
    "particle",
    "vfx",
    "trail",
    # 输入 / 构建 / 资源
    "input",
    "playerprefs",
    "addressable",
    "assetbundle",
    "resources",
    "streaming",
    "build",
    "editor",
    "serialize",
    "inspector",
    # 其它常见
    "coroutine",
    "async",
    "job",
    "burst",
    "ecs",
    "entity",
)


def _path_allowed(path: Path, allow: tuple[str, ...]) -> bool:
    """
    Manual 整本手册默认放行（助手不限性能扮演）；
    ScriptReference 等仍按关键词，避免海量枚举/无关 API。
    """
    key = str(path).lower().replace("\\", "/")
    if "/manual/" in key:
        return True
    return any(a in key for a in allow)


def load_docs(
    dir_path: str | Path,
    *,
    path_allow: tuple[str, ...] | None = DEFAULT_PATH_ALLOW,
    max_docs: int | None = None,
) -> list[dict]:
    root = Path(dir_path)
    if not root.is_dir():
        raise FileNotFoundError(f"知识库目录不存在: {root}")

    docs: list[dict] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        suf = path.suffix.lower()
        if suf not in (".md", ".txt", ".html", ".htm"):
            continue
        if path_allow is not None and not _path_allowed(path, path_allow):
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if suf in (".html", ".htm"):
            text = html_to_text(raw)
        else:
            text = raw.strip()
        if len(text) < 80:
            continue
        docs.append({"source": str(path), "text": text})
        if max_docs is not None and len(docs) >= max_docs:
            break
    return docs
