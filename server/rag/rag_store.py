"""Chroma 向量库：落在 server/knowledge/chroma，支持清空重建。

嵌入默认 `server/knowledge/models/bge-base-en-v1.5`；
缺省时回退 text2vec-base-chinese。可用 RAG_EMBED_MODEL 覆盖。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

_ROOT = Path(__file__).resolve().parents[2]
_CHROMA_DIR = _ROOT / "server" / "knowledge" / "chroma"
DEFAULT_COLLECTION = "unity_docs"
_COLLECTION = DEFAULT_COLLECTION  # 兼容旧引用
_DEFAULT_EMBED = _ROOT / "server" / "knowledge" / "models" / "bge-base-en-v1.5"
# 保留本地中文模型，需要时: set RAG_EMBED_MODEL=...\text2vec-base-chinese
_FALLBACK_EMBED = (
    _ROOT / "server" / "knowledge" / "models" / "text2vec-base-chinese"
)

_collections: dict = {}
_model: SentenceTransformer | None = None


def _resolve_model_path() -> str:
    override = (os.environ.get("RAG_EMBED_MODEL") or "").strip()
    if override:
        return override
    if _DEFAULT_EMBED.is_dir():
        return str(_DEFAULT_EMBED)
    if _FALLBACK_EMBED.is_dir():
        print(
            f"未找到 {_DEFAULT_EMBED.name}，回退 {_FALLBACK_EMBED.name}",
            flush=True,
        )
        return str(_FALLBACK_EMBED)
    raise FileNotFoundError(
        f"嵌入模型目录不存在: {_DEFAULT_EMBED}\n"
        "请把 bge-base-en-v1.5 放到 server/knowledge/models/ 下，"
        "或设置 RAG_EMBED_MODEL。"
    )


def _embedder() -> SentenceTransformer:
    global _model
    if _model is None:
        path = _resolve_model_path()
        local_only = Path(path).is_dir()
        print(f"加载嵌入模型: {path} local_files_only={local_only}", flush=True)
        _model = SentenceTransformer(path, local_files_only=local_only)
    return _model


def _embed(texts: list[str]) -> list[list[float]]:
    return _embedder().encode(texts, normalize_embeddings=True).tolist()


def _client() -> chromadb.PersistentClient:
    _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(_CHROMA_DIR))


def get_collection(name: str = DEFAULT_COLLECTION, *, create: bool = True):
    """按名取 collection；create=False 且不存在则返回 None。"""
    key = (name or DEFAULT_COLLECTION).strip() or DEFAULT_COLLECTION
    if key in _collections:
        return _collections[key]
    client = _client()
    if create:
        col = client.get_or_create_collection(name=key)
    else:
        try:
            col = client.get_collection(name=key)
        except Exception:
            return None
    _collections[key] = col
    return col


def _get_collection():
    """兼容旧调用：默认 unity_docs。"""
    return get_collection(DEFAULT_COLLECTION, create=True)


def reset_collection(name: str = DEFAULT_COLLECTION) -> None:
    """重建索引前清空指定集合，避免 id 冲突。"""
    key = (name or DEFAULT_COLLECTION).strip() or DEFAULT_COLLECTION
    _collections.pop(key, None)
    client = _client()
    try:
        client.delete_collection(key)
    except Exception:
        pass
    _collections[key] = client.get_or_create_collection(name=key)


def add_chunks(
    chunks: list[dict],
    *,
    batch_size: int = 64,
    collection: str = DEFAULT_COLLECTION,
) -> int:
    if not chunks:
        return 0
    col = get_collection(collection, create=True)
    n = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c["text"] for c in batch]
        embeddings = _embed(texts)
        ids = []
        metadatas = []
        for j, c in enumerate(batch):
            raw_id = f"{collection}:{c['source']}#{i + j}"
            ids.append(hashlib.sha1(raw_id.encode("utf-8")).hexdigest())
            metadatas.append(
                {
                    "source": c["source"],
                    "collection": collection,
                }
            )
        col.upsert(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        n += len(batch)
        print(f"  upsert {n}/{len(chunks)}", flush=True)
    return n
