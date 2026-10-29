"""把 RenderDoc CHM 解出的 HTML 抽成 Markdown，写入 knowledge/clean/renderdoc-1.46。

不改 extracted。用法（仓库根目录）:
  python -m server.rag.clean_renderdoc_docs
  python -m server.rag.rag_build --src server/knowledge/clean/renderdoc-1.46 --collection renderdoc_docs
"""
from __future__ import annotations

import argparse
import html as html_lib
import re
from pathlib import Path

from server.rag.rag_loader import html_to_text

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SRC = _ROOT / "server" / "knowledge" / "extracted" / "renderdoc-1.46"
_DEFAULT_OUT = _ROOT / "server" / "knowledge" / "clean" / "renderdoc-1.46"

# 索引页 / 致谢噪声大，正文教学价值低
_SKIP_NAMES = frozenset(
    {
        "genindex.html",
        "credits_acknowledgements.html",
        "search.html",
    }
)


def _title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    if not m:
        return ""
    t = html_lib.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
    t = re.sub(r"\s*[—\-]\s*RenderDoc.*$", "", t, flags=re.I).strip()
    return t


def main() -> None:
    ap = argparse.ArgumentParser(description="Clean RenderDoc HTML → Markdown")
    ap.add_argument("--src", type=Path, default=_DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = ap.parse_args()
    src: Path = args.src
    out: Path = args.out
    if not src.is_dir():
        raise SystemExit(
            f"目录不存在: {src}\n"
            "请先用 7-Zip / hh -decompile 把 renderdoc.chm 解到该目录。"
        )

    n = 0
    for path in sorted(src.rglob("*.html")):
        if path.name.lower() in _SKIP_NAMES:
            continue
        # Sphinx 静态/搜索页不要
        rel = path.relative_to(src).as_posix()
        if rel.startswith("_static/") or "/_static/" in rel:
            continue
        raw = path.read_text(encoding="utf-8", errors="ignore")
        body = html_to_text(raw)
        if len(body) < 80:
            continue
        title = _title(raw)
        md = f"# {title}\n\n{body}\n" if title else f"{body}\n"
        dest = out / path.relative_to(src)
        dest = dest.with_suffix(".md")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(md, encoding="utf-8")
        n += 1
    print(f"写入 {n} 个 md → {out}")


if __name__ == "__main__":
    main()
