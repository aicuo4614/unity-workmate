# Unity 助手编排骨架

一张图、多扮演。当前默认 `scene_perf` 走瘦图（可复用节点名）。

## 节点（当前图）

```text
START → greet → hitl → intent → analyze | act
                      ↑______________|_________|
```

| 节点 | 含义 |
|---|---|
| `greet` | 开场口播，无工具 |
| `hitl` | 唯一等人闸门（`interrupt_before`） |
| `intent` | LLM + 扮演 intent hint 分流（不调 Unity）→ `analyze` / `act` |
| `analyze` | 只读。具体问题和整场分析都在这里：工具整包绑定，模型按对话挑 |
| `act` | 批准后的占位：不绑工具、不改场景 |

`hitl` 之后**一律**进 `intent`。无 `report` / `END`：会话常态停在 `hitl`。

## HITL

- `interrupt_before=["hitl","act"]`
- 等人：`need_clarify` + `gate=hitl`；resume `say`/`ask` + content → `intent`
- 改场景前：`need_approval`；`approve` 进 `act`，`skip` 回 `hitl`

## 扮演能力

`server/graph/flow.py`：`can_act` 等。只读扮演勿路由到 `act`。

## 包边界

- `server/graph/`：编译图、跑图、流式桥、边路由、nodes
- `server/roles/`：扮演业务 + `registry/`
- `server/evidence/`：本局证据袋
- `server/rag/`：本地文档检索（analyze ask 注入；非图节点）
- `server/tools/`：MCP 分服务器连接；`tools_for_analyze` 按扮演绑定整台 MCP

## 权威实现

`server/graph/graph_manager.py`、`runner.py`、`nodes/*`。
