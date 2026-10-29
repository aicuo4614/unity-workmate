"""按段落优先切块，避免把 HTML 剥完后的长文硬切到句子中间。"""


def split_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if not buf:
            buf = p
            continue
        if len(buf) + 1 + len(p) <= chunk_size:
            buf = buf + "\n" + p
        else:
            chunks.append(buf)
            if overlap > 0 and len(buf) > overlap:
                tail = buf[-overlap:]
                buf = tail + "\n" + p
            else:
                buf = p
    if buf:
        chunks.append(buf)

    out: list[str] = []
    for c in chunks:
        if len(c) <= chunk_size * 2:
            out.append(c)
            continue
        start = 0
        while start < len(c):
            end = start + chunk_size
            out.append(c[start:end])
            start = max(end - overlap, start + 1)
    return out
