# 知识库目录

- `raw/`：官方 zip（勿提交）
- `extracted/`：解压后的 HTML（勿提交，只作清洗源）
- `clean/`：从 HTML 抽出的干净 Markdown（勿提交；建库默认读这里）
- `models/`：本地嵌入 / 重排权重（勿提交）
  - `bge-base-en-v1.5` — 默认 embedding（英文）
  - `text2vec-base-chinese` — 备用中文 embedding
  - `bge-reranker-base` — CrossEncoder rerank
- `chroma/`：Chroma 向量库（勿提交）

## 筛选规则（extracted → clean）

- **Manual**：整本放行（不限性能专题）
- **ScriptReference**：路径关键词白名单（渲染/物理/动画/UI/音频/资源/编辑器等），避免海量枚举 API

## 清洗 → 建库（2022.3）

一键（仓库内双击或命令行）：

```text
server\rag\clean_unity_docs.bat      # 只洗 clean/
server\rag\build_unity_docs.bat      # 只重嵌 unity_docs 集合
server\rag\rebuild_unity_docs.bat    # clean + build
```

或手动：

```text
# 1) HTML → clean md（不改 extracted）
uv run python -m server.rag.rag_clean

# 2) 对 clean 建索引（默认模型 bge-base-en-v1.5）
uv run python -m server.rag.rag_build
```

`rag_clean` 默认按上面规则过滤；连 ScriptReference 也不滤时加 `--no-filter`（很大）。

## RenderDoc 文档（可选第二集合）

从本机安装目录的 `renderdoc.chm` 解出后入库，集合名 `renderdoc_docs`（与 `unity_docs` 并列检索）。

```text
# 1) 解压 CHM → extracted（已有则跳过）
#    7z x "D:\Work\RenderDoc\renderdoc.chm" -oserver\knowledge\extracted\renderdoc-1.46

# 2) HTML → clean md
uv run python -m server.rag.clean_renderdoc_docs

# 3) 建索引（不会清空 unity_docs）
uv run python -m server.rag.rag_build --src server/knowledge/clean/renderdoc-1.46 --collection renderdoc_docs
```

## Huawei Graphics Profiler 文档（可选第三集合）

安装目录 `D:\Work\GraphicsProfiler\Graphics Profiler` **没有** CHM/HTML 手册。
`resources\app.asar` 的帮助入口是打开华为官网；`FrameProfiler.exe` 里只有少量界面说明。

```text
# 内嵌 HTML 已在 extracted/.../frameprofiler-embedded/
# HTML → clean md
uv run python -m server.rag.clean_huawei_gp_docs

# 建索引（不会清空其它集合）
uv run python -m server.rag.rag_build --src server/knowledge/clean/huawei-graphics-profiler --collection huawei_gp_docs
```

入口：`server/knowledge/clean/huawei-graphics-profiler/index.md`  
安装包内说明：`.../local/README.md`

## 检索

流程：中文问句可先 LLM 改写成英文 → Chroma 粗召回 → `bge-reranker-base` 精排。  
默认集合：`unity_docs` + `renderdoc_docs`（后者未建库时自动跳过）。

```text
uv run python -c "from server.rag.rag_retriever import search; print(search('URP RenderQueue draw order'))"
uv run python -c "from server.rag.rag_retriever import search; print(search('how to capture on Android', collections=['renderdoc_docs']))"
```

环境变量：`RAG_EMBED_MODEL` / `RAG_RERANK_MODEL` 可覆盖模型路径。
