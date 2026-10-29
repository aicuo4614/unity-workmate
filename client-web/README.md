# client-web

场景优化助手 Web 客户端（Vite + React + Ant Design）。

## 开发

双击仓库根下约定入口即可：

- 服务端：`server\run.bat` → http://127.0.0.1:8000  
- **客户端：`client\run.bat`** → http://127.0.0.1:5173（Vite，已 proxy API）

或手动：

```bash
cd client-web
npm install
npm run dev
```

## 约定

见仓库 `.cursor/rules/ailearning-client-web.mdc`：services 唯一网络出口、i18n 禁硬编码。
