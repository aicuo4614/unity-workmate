# RenderDoc 文件转换（第一版）

本功能不调用大模型、不接入 LangGraph 对话节点，也不要求连接 Android MCP。
设备性能会话的底部工具栏新增 RenderDoc，可选择 RDC 文件或输入服务端本机路径。

## 数据流程

原始 capture.rdc → RenderDoc 内置 Python API 回放 → capture.detaildata → Python 确定性聚合 → capture.aidata。
图片独立保存，摘要引用图片，不把图片二进制展开进 JSON。
原始文件、SHA-256、转换时间、提取器/RenderDoc 版本保留，可追溯到真实 EID 和资源 ID。

优先用 qrenderdoc --python 启动独立转换进程，避免与服务器 Python 3.13 的 ABI 冲突。
可填远端回放服务器地址。未成功回放时，使用 renderdoccmd 结构导出做有限基础解析。
基础解析当前仅支持 OpenGL/OpenGLES 绘制与不可变纹理存储的部分信息；
其他 API 的基础解析统计明确为 null，需要成功回放才能提取。
chunk_index 与 event_id 是不同字段，绝不通过固定偏移推算 EID。

## 配置与使用

服务端进程的 RENDERDOC_DIR 环境变量可指定 RenderDoc 安装目录。
优先使用该目录，然后 PATH、Program Files/RenderDoc，及本机已有 D:/Work/RenderDoc。
使用原项目依赖，不增加 pip/npm 依赖。部署后重启后端和前端。

RenderDoc Tab：
1. 本机路径导入或选择 RDC 文件上传（上限 2 GiB）。
2. 查看后台转换进度/抓帧历史；无需启动模型会话。
3. 查看缩略图、末次绘制输出、阶段/Shader/资源统计。
4. 下载 .detaildata 或 .aidata。

数据按独立 UUID 保存在 server/logs/renderdoc/<id>/。
这批文件不按对话会话分组，不自动删除原始抓帧。
状态 done 代表完整回放提取成功；partial 代表基础解析成功，必须同时看 capabilities 和 limitations。
pipeline=false 时 coverage.pipeline_failures 保留具体失败事件。
服务重启后，未完成的转换标为 interrupted，已有原始文件可下载并重新导入。

## 第一版的数据边界

- .detaildata 保留全部已提取绘制/计算记录、动作和标记树、资源/使用记录、管线字段。
- 原始 RDC 保留完整 API 流；基础事件索引并不是完整 API 调用的无损 JSON 展开。
- .aidata 保留所有有绘制/计算的阶段及 Shader 使用汇总；
  默认最多 20 个大资源及 20 个大几何绘制，保留明细引用。
- 资源清单含纹理和缓冲；byteSize 为 RenderDoc 描述的逻辑大小，不能当作驱动实际分配或应用总显存。
- 不采集 GPU 计时，不把 API duration 或回放机器耗时冒充原手机耗时。
- 不生成“Shader 丢失”等根因结论，也未导出 Shader 源码。
- 末次绘制输出有独立标签，不冒称系统最终屏幕截图。
- 远端回放路径已接入，尚未进行真机远端回归。GPU 调试和前后帧对比不在此版。

## 接口

- GET /mcp/renderdoc/environment
- GET /mcp/renderdoc/history
- POST /mcp/renderdoc/import：JSON {path, remote_host}
- POST /mcp/renderdoc/upload?name=test.rdc：application/octet-stream；remote_host 可选
- GET /mcp/renderdoc/<id>
- GET /mcp/renderdoc/<id>/data?kind=aidata|detaildata
- GET /mcp/renderdoc/<id>/files/<name>：仅白名单文件

## 验证记录

用用户 test.rdc 完成官方 Python API 本机回放与基础解析双路径测试：
32 draw calls、3 dispatch、20,195 submitted triangles。
完整回放取得 112 个纹理和缓冲资源；基础解析取得 36 个纹理描述，覆盖范围显式标明。
完整提取所有绘制的管线/绑定，无 pipeline_failures，GPU 时间为 null。
验证了 JSON 内不含 SWIG 内存地址、Shader 程序名及 EID 121 的描边绘制。
9 项数据契约/状态测试通过，TypeScript 检查及 Vite 生产构建通过。
隔离验证借用原工程只读依赖路径；验证专用配置不属于补丁。
HTTP 端到端和浏览器视觉检查未完成；当前执行沙箱内 asyncio 运行阻塞，
不能据此声称实际 HTTP 请求链路已验证。原机运行后应回归导入、轮询、下载。
测试命令（原工程）：
uv run python -m unittest server.tests.test_renderdoc server.tests.test_renderdoc_contract -v
