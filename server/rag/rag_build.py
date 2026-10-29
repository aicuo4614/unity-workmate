"""构建 Unity 文档向量索引。

用法（仓库根目录）:
  python -m server.rag.rag_clean   # HTML → clean md（不改 extracted）
  python -m server.rag.rag_build   # 默认读 clean/
"""
from __future__ import annotations

import argparse
from pathlib import Path

from server.rag.rag_loader import DEFAULT_PATH_ALLOW, load_docs
from server.rag.rag_splitter import split_text
from server.rag.rag_store import add_chunks, reset_collection

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SRC = _ROOT / "server" / "knowledge" / "clean" / "unity-2022.3"
_FALLBACK_SRC = _ROOT / "server" / "knowledge" / "extracted" / "unity-2022.3"


def main() -> None:
    ap = argparse.ArgumentParser(description="Build Unity docs Chroma index")
    ap.add_argument(
        "--src",
        type=Path,
        default=None,
        help="文档根目录（默认 knowledge/clean/unity-2022.3）",
    )
    ap.add_argument(
        "--no-filter",
        action="store_true",
        help="不过滤路径（全量，很慢且噪声大）",
    )
    ap.add_argument("--max-docs", type=int, default=None)
    ap.add_argument(
        "--collection",
        default=None,
        help="Chroma 集合名（默认 unity_docs；RenderDoc 用 renderdoc_docs）",
    )
    args = ap.parse_args()

    src = args.src
    if src is None:
        src = _DEFAULT_SRC if _DEFAULT_SRC.is_dir() else _FALLBACK_SRC
    if not src.is_dir():
        raise SystemExit(
            f"目录不存在: {src}\n请先: python -m server.rag.rag_clean"
        )

    # clean 已是白名单产物；对 clean 不再二次过滤。extracted 仍过滤。
    allow = None if args.no_filter else DEFAULT_PATH_ALLOW
    if "clean" in str(src).replace("\\", "/"):
        allow = None
    from server.rag.rag_store import DEFAULT_COLLECTION

    collection = (args.collection or DEFAULT_COLLECTION).strip() or DEFAULT_COLLECTION
    print(
        f"加载: {src} collection={collection} "
        f"filter={'off' if allow is None else 'perf-related'}"
    )
    docs = load_docs(src, path_allow=allow, max_docs=args.max_docs)
    print(f"文档数: {len(docs)}")

    all_chunks: list[dict] = []
    for doc in docs:
        for c in split_text(doc["text"]):
            if len(c.strip()) < 40:
                continue
            all_chunks.append({"text": c, "source": doc["source"]})
    print(f"块数: {len(all_chunks)}")
    if not all_chunks:
        print("没有可写入的块，跳过")
        return

    print(f"清空并重建 collection={collection}…")
    reset_collection(collection)
    add_chunks(all_chunks, collection=collection)
    print("索引完成")


if __name__ == "__main__":
    main()
