from server.rag.rag_loader import DEFAULT_PATH_ALLOW, _path_allowed
from server.rag.rag_store import _get_collection
from pathlib import Path

needles = [
    "Material-renderQueue",
    "SL-SubShaderTags",
    "SL-PassTags",
    "DrawCallBatching",
    "shader-Transparent",
    "2DSorting",
    "class-SortingGroup",
]

root = Path("server/knowledge/extracted/unity-2022.3")
print("--- path allow? ---")
for p in root.rglob("*.html"):
    name = p.name
    for n in needles:
        if n.lower() in name.lower() or n.lower() in str(p).lower():
            ok = _path_allowed(p, DEFAULT_PATH_ALLOW)
            print(f"  allow={ok}  {p.as_posix().split('extracted/',1)[-1]}")

col = _get_collection()
print("count", col.count())
# Chroma where $contains may vary by version; fallback scan a sample via get all ids is heavy.
# Use query with English keyword instead.
from server.rag.rag_store import _embed

for q in ["Material renderQueue", "ShaderLab Queue tag", "transparent sorting order"]:
    emb = _embed([q])
    r = col.query(query_embeddings=emb, n_results=5)
    docs = (r.get("documents") or [[]])[0]
    metas = (r.get("metadatas") or [[]])[0]
    print(f"\nEN query: {q}")
    for i, m in enumerate(metas):
        src = ((m or {}).get("source") or "").replace("\\", "/")
        if "extracted/" in src:
            src = src.split("extracted/", 1)[-1]
        print(f"  [{i+1}] {src[-100:]}")
