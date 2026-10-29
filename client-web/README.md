# client-web

Unity Workmate 的 Web 客户端（Vite + React + Ant Design）：HITL 对话、扮演侧栏 / 底栏，不是第二套业务脑。

## 开发

仓库约定入口：

- 服务端：`server/run.bat` → http://127.0.0.1:8000  
- 客户端：`client-web/run.bat` → http://127.0.0.1:5173（Vite，已 proxy API）

或手动：

```bash
cd client-web
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

## 约定

见仓库 `.cursor/rules/ailearning-client-web.mdc`：`src/services/` 唯一网络出口、文案走 `src/locales/`、禁止组件直接 `fetch` / `EventSource`。
