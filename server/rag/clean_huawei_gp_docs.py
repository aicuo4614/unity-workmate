"""把安装目录里抽出的 Frame Profiler HTML 说明洗成 Markdown。

安装包不带整本用户手册（帮助菜单打开华为官网）。本地能导出的是
`FrameProfiler.exe` 内嵌的界面说明，先落到
`knowledge/extracted/huawei-graphics-profiler/frameprofiler-embedded/`。

用法（仓库根目录）:
  python -m server.rag.clean_huawei_gp_docs
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from server.rag.rag_loader import html_to_text

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SRC = (
    _ROOT
    / "server"
    / "knowledge"
    / "extracted"
    / "huawei-graphics-profiler"
    / "frameprofiler-embedded"
)
_DEFAULT_OUT = (
    _ROOT / "server" / "knowledge" / "clean" / "huawei-graphics-profiler" / "local"
)


def _title(text: str) -> str:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = re.sub(r"\s+", " ", line)
    return line[:80] or "help"


def main() -> None:
    ap = argparse.ArgumentParser(description="Clean Huawei GP embedded HTML → Markdown")
    ap.add_argument("--src", type=Path, default=_DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = ap.parse_args()
    src: Path = args.src
    out: Path = args.out
    if not src.is_dir():
        raise SystemExit(
            f"目录不存在: {src}\n"
            "请先从 FrameProfiler.exe 抽出 <html> 说明到该目录。"
        )
    n = 0
    for path in sorted(src.glob("*.html")):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        body = html_to_text(raw).strip()
        if len(body) < 40:
            continue
        title = _title(body)
        md = (
            f"# {title}\n\n"
            f"来源：`D:\\Work\\GraphicsProfiler\\Graphics Profiler\\frame_profiler\\FrameProfiler.exe` 内嵌说明（{path.name}）。\n\n"
            f"{body}\n"
        )
        dest = out / path.with_suffix(".md").name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(md, encoding="utf-8")
        n += 1
    print(f"写入 {n} 个 md → {out}")


if __name__ == "__main__":
    main()
