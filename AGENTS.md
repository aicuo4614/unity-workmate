# AGENTS.md

本仓库给 **Codex / 其他 Agent** 的入口说明。Cursor 细则仍在 `.cursor/rules/ailearning-*.mdc`；两边冲突时以权威长文与本文件硬性条款为准。

权威编排：`server/docs/unity_assistant_loop.md`  
启动与环境：`readme.md`

---

## 这是什么

Unity / 真机性能助手：**FastAPI + LangGraph 瘦图**，经 **MCP** 连编辑器或 Android；浏览器 `client-web` 只做 HITL 与口播展示，不是第二套业务脑。

默认扮演：`scene_perf`。真机扮演：`device_perf`（绑 `android-mcp`）。

---

## 包地图

```
common/                 # 请求模型、流式事件名
server/
  infra/                # checkpoint_manager / run_log_manager
  graph/                # 瘦图：compile、runner、stream_bridge、flow、nodes
  roles/ + prompts/     # 扮演业务（禁止渗进通用层）
  evidence/             # 本局证据袋（state 策略）
  tools/                # MCP 连接 / 绑定 / android_panel / perfetto / profiler
  mcp/mcp.json          # 服务端运行时 MCP 清单（权威）
client-web/             # Vite+React+Ant
```

---

## 编排硬性（防耦合）

```
START → greet → hitl → intent → analyze | act
                  ↑______________|_________|
```

- **一张图、节点名固定**；换扮演不换图骨架。`interrupt_before=["hitl","act"]`。
- **无** `report` / `END` / 仓库默认 baseline 成功口径；常态停在 `hitl`。
- 有状态能力走独立 Manager：`get_checkpointer()` / `begin_run` / `init_graph` / `init_tools`；禁止在 `graph_manager` 里直接 `InMemorySaver()`，禁止 listener 里 compile。
- **通用层禁止** `from …roles.scene_perf_* import` 解析字段；只调 `build_report` / `summarize_*`。
- analyze：`tools.tools_for_analyze(role)` → **该扮演对应 MCP 的全部工具**（不按名字裁剪）。执行站暂不绑工具。
- 新扮演清单见 `.cursor/rules/ailearning-role-extension.mdc`；流式/HITL 见 `ailearning-stream-hitl.mdc`。

自检：换一个扮演还成立吗？不成立 → 进 `roles/` 或 prompts，不改图骨架。

---

## MCP

### 配置落点

| 文件 | 谁用 | 说明 |
|---|---|---|
| `server/mcp/mcp.json` | **本服务 FastAPI 运行时** | Unity URL + `android-mcp`（uvx）；编排端连 MCP 以此为准 |
| `.codex/config.toml` | **Codex**（CLI / IDE / Desktop） | `[mcp_servers.*]`；与上表对齐；**仅 trusted 项目加载** |
| `.cursor/mcp.json` | Cursor IDE | 本机调试用；不是服务端真源 |

当前 MCP 条目（三处应对齐）：

- `kabu-unity-mcp` → `http://127.0.0.1:3828/mcp`
- `android-mcp` → `uvx android-mcp`（真机分析站整包绑定）

Codex 首次打开本仓若未信任：在 Codex 里 Trust 本仓库，或在 `~/.codex/config.toml` 加：

```toml
[projects.'E:\Project\AIProject\AILearning']
trust_level = "trusted"
```

### 连接与绑定边界

- 连接：`server/tools` 的 `init_tools` / `reconnect_mcp` / `mcp_status`。**一台失败不得拆掉其他服务器**。
- 前端**禁止**直连 MCP 端口 / 调 adb；经服务端 HTTP（含 `/mcp/android/*`）。
- `android-mcp`：**侧栏先扫设备再选机连接**；启动时不自动连；分析站绑定该 MCP **全部**工具，不按工具名再滤。
- 扮演 → 服务器：`server/tools/bind.py` 的 `_ROLE_SERVER`（未登记默认 Unity；`device_perf` → Android）。
- Perfetto / Profiler 采数在 `server/tools/perfetto_capture`、`profiler_capture`（HTTP 面板用）；**不要塞进 graph 节点**；对话只喂裁后的 `.aidata`。

### kabu-unity-mcp（Unity 能力）

本仓是 **编排端**；MCP 包源码不在本 git 树：

| 项 | 路径 |
|---|---|
| MCP 包 | `F:/Git/kabu_mobile/kabu-unity-mcp`（UPM `com.kabu.unity-mcp`） |
| 联调工程 | `F:/Git/kabu_mobile/client-assets`（settings 端口 **3828**） |
| Agent 用法 | `.cursor/rules/kabu-unity-mcp.mdc` |
| 扩展清单 | `.cursor/rules/kabu-unity-mcp-dev.mdc` |

调用以 **MCP tool schema** 为准，不为每个 tool 写 skill。

| 放 AILearning | 放 kabu-unity-mcp |
|---|---|
| LangGraph、扮演 prompt、HITL、SSE、成功口径 | Editor Ops / Handler / Server Tool / Catalog |
| 观测摘要、run 日志 | 采数稳定性、新编辑器副作用 |

采数纪律（帧诊）：

1. **不要**调 `unity_bring_editor_to_foreground`；依赖 Run In Background。
2. 优先 `unity_perf_capture_frame`；Render 全 0 勿当成「Batches=0」。
3. 改 MCP **Server~**：在包内 `Server~` 主动 `dotnet build`；DLL 占用则重启 Server 并让 Unity 重连。改 **Editor**：靠 Unity Domain Reload。
4. 新增 tool：Server Tool + Ops + Handler + Bridge + Catalog +（能写就写）EditMode 测；禁止巨型 `manage_*`；有副作用写清 Description。

规格缺口长文：`server/docs/mcp_perf_tool_gaps.md`。

---

## 代码习惯（摘要）

- 简体中文注释：关键处写清「为什么」；禁止废话注释与大段死代码。
- 模块顶短 docstring（职责 + 不做什么）；节点顶 `phase_name`；编排薄、业务进 `roles/` / prompts。
- LLM：填结构用 `ainvoke`；口播由代码拼或二次润色，避免内部 JSON 直灌 client。
- 前端：`client-web` 组件禁止直接 `fetch`/`EventSource`；走 `src/services/`；文案走 `src/locales/`（见 `ailearning-client-web.mdc`）。

---

## 联调自检

1. Unity（client-assets）MCP 已连，端口与 `server/mcp/mcp.json` 一致。
2. `uv run uvicorn …` → `GET /health` 看 `mcp_ok` / tool_count。
3. 有副作用的采数：测完 disable Profiler / Frame Debugger，按需退出 Play。
4. 改 MCP 实现：提交在 **kabu-unity-mcp** 仓库；本仓只改编排/用法/rule 时各自提交。
