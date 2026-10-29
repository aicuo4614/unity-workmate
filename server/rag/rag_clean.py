"""把 Unity 官方 HTML 抽成干净 Markdown，写入 knowledge/clean（不改 extracted）。

用法（仓库根目录）:
  python -m server.rag.rag_clean
"""
from __future__ import annotations

import argparse
import html as html_lib
import re
from html.parser import HTMLParser
from pathlib import Path

from server.rag.rag_loader import DEFAULT_PATH_ALLOW, _path_allowed

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SRC = _ROOT / "server" / "knowledge" / "extracted" / "unity-2022.3"
_DEFAULT_OUT = _ROOT / "server" / "knowledge" / "clean" / "unity-2022.3"

# 整段跳过：用 class token 精确匹配（避免 header 误伤 header-wrapper 外逻辑）
_SKIP_CLASS_TOKENS = frozenset(
    {
        "nextprev",
        "sidebar",
        "toolbar",
        "header",
        "header-wrapper",
        "footer",
        "feedback",
        "cookie",
        "lang-switcher",
        "version-number",
        "otherversionscontent",
        "mobilelogo",
        "search-form",
        "sidebar-search-form",
        "breadcrumbs",
        "mb20",
    }
)

_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

# 页内反馈表单常见固定句（抽完后再剥）
_NOISE_LINE_RE = re.compile(
    r"^(Suggest a change|Success!|Submission failed|Your name|Your email|"
    r"Thank you for helping us improve.*|For some reason your suggested change.*|"
    r"Close|Cancel|Submit suggestion|Switch to Manual|Switch to Scripting)$",
    re.I,
)


def _page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    if not m:
        return ""
    title = html_lib.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
    title = re.sub(
        r"^Unity\s*-\s*(Manual|Scripting API)\s*:\s*",
        "",
        title,
        flags=re.I,
    ).strip()
    return title


def _extract_content_wrap_inner(html: str) -> str:
    """只切出 #content-wrap 内部 HTML，避免页头/侧栏把解析器深度弄乱。"""
    m = re.search(
        r'<div[^>]*\bid=["\']content-wrap["\'][^>]*>',
        html,
        flags=re.I,
    )
    if not m:
        return ""
    i = m.end()
    depth = 1
    # 只对 div 做深度，足够框住 Unity 正文容器
    for mm in re.finditer(r"</?div\b[^>]*>", html[i:], flags=re.I):
        token = mm.group(0)
        if token.startswith("</"):
            depth -= 1
            if depth == 0:
                return html[i : i + mm.start()]
        else:
            # <div ... /> 极少；按开标签计
            if not token.endswith("/>"):
                depth += 1
    return html[i:]


class _SectionTextExtractor(HTMLParser):
    """在已切出的 content-wrap 片段上抽 .section 文本。"""

    def __init__(self) -> None:
        super().__init__()
        self._section_depth = 0
        self._skip_depth = 0
        self._parts: list[str] = []
        self._saw_section = False
        self._fallback_parts: list[str] = []  # 无 section 时收全部

    def handle_starttag(self, tag: str, attrs) -> None:
        self._on_start(tag, attrs, void=False)

    def handle_startendtag(self, tag: str, attrs) -> None:
        # void / XML 自闭合：不增加 skip/section 深度
        self._on_start(tag, attrs, void=True)

    def _on_start(self, tag: str, attrs, *, void: bool) -> None:
        ad = {k: (v or "") for k, v in attrs}
        cls = ad.get("class", "")
        tokens = {t.lower() for t in cls.split() if t}

        if self._skip_depth:
            if not void and tag not in _VOID:
                self._skip_depth += 1
            return

        if tag in ("script", "style", "nav", "noscript"):
            if not void:
                self._skip_depth = 1
            return

        if tokens & _SKIP_CLASS_TOKENS:
            if not void:
                self._skip_depth = 1
            return

        if self._section_depth == 0 and "section" in tokens:
            self._section_depth = 1
            self._saw_section = True
            return

        if self._section_depth and not void and tag in (
            "div",
            "section",
            "article",
            "table",
            "ul",
            "ol",
            "dl",
        ):
            self._section_depth += 1

        if tag in ("h1", "h2", "h3", "h4", "h5", "p", "li", "tr", "br", "pre"):
            if self._capturing():
                self._parts.append("\n")
            elif not self._saw_section:
                self._fallback_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag not in _VOID:
                self._skip_depth = max(0, self._skip_depth - 1)
            return

        if self._section_depth and tag in (
            "div",
            "section",
            "article",
            "table",
            "ul",
            "ol",
            "dl",
        ):
            self._section_depth -= 1
            if self._capturing():
                self._parts.append("\n")
            return

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        t = data.strip()
        if not t:
            return
        if self._capturing():
            self._parts.append(t)
        elif not self._saw_section:
            self._fallback_parts.append(t)

    def _capturing(self) -> bool:
        return self._section_depth > 0

    def text(self) -> str:
        parts = self._parts if self._saw_section else self._fallback_parts
        raw = " ".join(parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _strip_noise_lines(body: str) -> str:
    lines = []
    for line in body.splitlines():
        s = line.strip()
        if not s:
            lines.append("")
            continue
        if _NOISE_LINE_RE.match(s):
            continue
        if s.startswith("Please <a>") or "try again" in s and "suggested change" in s:
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def html_to_clean_markdown(html: str) -> str:
    """Unity 文档 HTML → 标题 + 正文纯文本。"""
    title = _page_title(html)
    inner = _extract_content_wrap_inner(html)
    if not inner:
        return ""

    parser = _SectionTextExtractor()
    try:
        parser.feed(inner)
        parser.close()
        body = parser.text()
    except Exception:
        body = re.sub(r"<[^>]+>", " ", inner)
        body = re.sub(r"\s+", " ", body).strip()

    body = _strip_noise_lines(body)
    if len(body) < 40:
        return ""

    lines = []
    if title:
        lines.append(f"# {title}")
        lines.append("")
    lines.append(body)
    return "\n".join(lines).strip() + "\n"


def clean_tree(
    src: Path,
    out: Path,
    *,
    path_allow: tuple[str, ...] | None = DEFAULT_PATH_ALLOW,
    max_docs: int | None = None,
) -> tuple[int, int]:
    if not src.is_dir():
        raise FileNotFoundError(f"源目录不存在: {src}")

    written = 0
    skipped = 0
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".html", ".htm"):
            continue
        if path_allow is not None and not _path_allowed(path, path_allow):
            skipped += 1
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            skipped += 1
            continue
        md = html_to_clean_markdown(raw)
        if len(md.strip()) < 80:
            skipped += 1
            continue
        rel = path.relative_to(src).with_suffix(".md")
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(md, encoding="utf-8")
        written += 1
        if written % 200 == 0:
            print(f"  clean wrote {written}…", flush=True)
        if max_docs is not None and written >= max_docs:
            break
    return written, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description="Unity HTML → clean Markdown")
    ap.add_argument("--src", type=Path, default=_DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    ap.add_argument("--no-filter", action="store_true")
    ap.add_argument("--max-docs", type=int, default=None)
    args = ap.parse_args()

    allow = None if args.no_filter else DEFAULT_PATH_ALLOW
    # 重建 clean：先清空旧输出，避免残留只有 ScriptReference 的半成品
    if args.out.exists():
        import shutil

        shutil.rmtree(args.out)
    args.out.mkdir(parents=True, exist_ok=True)

    print(
        f"clean: {args.src} → {args.out} "
        f"filter={'off' if allow is None else 'perf-related'}"
    )
    n, skip = clean_tree(
        args.src, args.out, path_allow=allow, max_docs=args.max_docs
    )
    print(f"完成: wrote={n} skipped={skip}")


if __name__ == "__main__":
    main()
