# MCP 性能工具缺口（给 kabu-unity-mcp 排期）

来源：AILearning 澄清问答实测。编排侧已能追问；**卡在采数粒度**，不是口播问题。

包路径：`F:/Git/kabu_mobile/kabu-unity-mcp`。落地须遵守 `kabu-unity-mcp-dev` 清单（Server Tool + Ops + Handler + Bridge + Catalog）。

---

## 1. 遮挡剔除：从「有没有烘焙」→「有没有省绘制」

### 现状

`unity_get_occlusion_info`（`OcclusionOps`）只能给：

- `OcclusionCullingData` 资产路径
- Area / Portal 数量
- `conclusion`: `bake_data_present` / `likely_enabled` / …

**回答不了**：运行时有没有剔物体、开关前后 Batches 差多少。

### 需求 A（优先）：开关对照 tool

建议新 tool（勿塞进现有 info 的大 `action=`）：

| 项 | 约定 |
|---|---|
| 名 | `unity_perf_occlusion_ab`（或 `unity_occlusion_toggle_capture`） |
| 副作用 | 临时改剔除开关 → 采帧 → **必须还原**；Description 写清 |
| 流程 | ① 记当前开关 ② 采 baseline capture（basics）③ 切到相反状态 ④ 再采 ⑤ 还原 ⑥ 回包 delta |
| 回包 | `before/after` 的 batches / drawCalls / setPass / tris；`delta.batches`；`restored: true`；失败写 `gaps` |
| 开关落点 | 优先查 `StaticOcclusionCulling` / 场景 Occlusion 相关 API；URP/相机若另有开关需在 Description 注明测的是哪一层 |
| 前置 | Play + Game 有画面（对齐 capture `strictBasics`） |

### 需求 B（可选）：info 增强

在现有 `unity_get_occlusion_info` 增字段（仍只读）：

- 当前是否启用 Static Occlusion（若 API 可读）
- 与激活场景绑定的 bake 资产（过滤非本场景路径）

不替代 A。

---

## 2. Overdraw：Play/Game A/B 层数贡献

### 现状

`unity_perf_get_overdraw`（`OverdrawAbHandlers` + `OverdrawOps`）在 **Play** 下：

- Game 相机 Additive RT → 整屏 `avgOverdraw` / `maxOverdraw` / `sumLayers`
- 透明优先候选（`candidateLimit`，默认 30）逐个关 `Renderer.enabled` → 再采 → 恢复
- `data.top[]`：`avgLayersDrop` / `maxLayersDrop`（关掉后整屏少了几层），`scoreKind=play_ab_layers`
- `data.top`：过绘贡献表；显示层 / 口播自建 markdown，回包不再带 `markdownTable`
- 编辑器近似，**非**真机 TBDR

### 验收

1. RoomScene_Op Play：`top` 含水/FX，且 `avgLayersDrop` 高于草/地形类不透明
2. 未 Play 时会自动进 Play
3. 与 capture/sweep busy 互斥

---

## 3. 优先级

| 序 | 项 | 价值 |
|---|---|---|
| ✅ | Overdraw TopN proxy | 已由 Play A/B 层数贡献替代 |
| ✅ | Overdraw Play A/B avgLayersDrop | 会话可点名「贡献几层」 |
| P0 | Occlusion A/B capture | 能回答「有没有起作用」 |
| P1 | Occlusion info 增强 | 少误判 bake 归属 |
| P2 | 真机 TBDR / ObjectID 单帧归属 | 精度；可后做 |

---

## 4. 验收（联调）

1. RoomScene_Op Play 下：`get_overdraw`（或 top tool）回包含 ≥10 行 `top`，path 可读  
2. AILearning 澄清追问「谁过绘大」→ 口播出现 Markdown 表  
3. `occlusion_ab`：开关前后 batches 有数字；`restored=true`；异常不留下关着的剔除  
4. Description / Catalog / Bridge 三处命名一致；Server~ `dotnet build` 通过  
