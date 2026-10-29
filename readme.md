# AILearning

Unity 编辑器助手：FastAPI + LangGraph 瘦图编排，经 MCP 连接 Unity（kabu-unity-mcp），浏览器端对话与 HITL。

当前默认扮演：`scene_perf`（场景性能优化）。权威编排说明见 [`server/docs/unity_assistant_loop.md`](server/docs/unity_assistant_loop.md)。

## 结构

```text
common/          共享配置、请求模型、流式事件名
server/          FastAPI + 图 + 扮演 + MCP 配置
client-web/      Vite + React 主前端
```

数据流（摘要）：

```text
greet → hitl → intent → analyze | act →（回 hitl）
```

`interrupt_before`：`hitl` / `act`。旧 checkpoint 与本图不兼容，请新开会话。

## 环境

- Python ≥ 3.13，[uv](https://github.com/astral-sh/uv)
- Node.js（前端）
- Unity + kabu-unity-mcp（默认端口见 `server/mcp/mcp.json`）
- 启动后在工作台右上角「设置 · 大模型配置」填写 DeepSeek API Key，测试连接并保存；未配置也可以启动服务。
- 配置保存在本机 `server/data/model_config.json`（Git 已忽略）；所有助手共用，从下一轮对话生效。查询接口不回传密钥。
- 兼容根目录 `.env`：`api_key` / `base_url` / `model` / `request_timeout`；网页保存后以本机配置为准。可选 `app_env=dev|prod`（默认 `dev`）。

```text
uv sync
cd client-web && npm install
```

## 启动

1. Unity 侧启动 MCP  
2. 服务端：双击 `server/run.bat`，或：

```text
uv run uvicorn server.main:app --reload --reload-dir server --reload-dir common --host 127.0.0.1 --port 8000
```

3. 前端：双击 `client-web/run.bat`，或：

```text
cd client-web
npm run dev -- --host 127.0.0.1 --port 5173
```

- API：http://127.0.0.1:8000 （`/docs`）  
- Web：http://127.0.0.1:5173  

可选：`cd client-web && npm run build` 后，服务端会把 `client-web/dist` 挂到 `/app`。

## 依赖

```text
uv add 包名
uv remove 包名
```

前端在 `client-web/` 下用 npm。

## 本地运行时数据（勿提交）

| 路径 | 说明 |
|---|---|
| `server/logs/app.log` | 服务端终端式日志 |
| `server/logs/latest.md` | **开发态**最近一局对话时间线（口播/站/工具摘要） |
| `server/logs/sessions/` | 开发态分局归档 |
| `client-web/logs/app.log` | 前端开发态 UI 事件（Vite 写入） |
| `server/data/` | checkpoint SQLite |
| `.venv/`、`client-web/node_modules/` | 环境 |

`.gitignore` 已忽略日志与 data。清会话状态可删 `server/data/checkpoints.sqlite*` 后重启服务端。  
查一局线索：优先读 `server/logs/latest.md`。
