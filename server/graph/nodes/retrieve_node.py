from server import state
from server.rag import rag_retriever

def retrieve_node(state: state.AgentState):
    last = state["messages"][-1]
    query = last.content
    # 粗召回 + CrossEncoder 重排；top_k 与 rag_retriever 默认一致
    hits = rag_retriever.search(query, top_k=5)

    if not hits:
        return {"context": ""}

    parts = [f"[来源: {h['source']}]\n{h['text']}" for h in hits]
    return {"rag_context": "\n\n---\n\n".join(parts)}