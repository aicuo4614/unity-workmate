/**
 * 前端日志：开发态打 console，并 POST 到 Vite 中间件写入 client-web/logs/app.log。
 * 发布版 no-op（除 console 外也不请求）。
 */
import { appConfig } from "../config";

export function clientLog(message: string, detail?: unknown): void {
  if (!appConfig.clientLogEnabled) return;
  if (detail !== undefined) {
    console.debug(`[client] ${message}`, detail);
  } else {
    console.debug(`[client] ${message}`);
  }
  const line =
    detail !== undefined
      ? `${message} ${safeJson(detail)}`
      : message;
  void fetch("/__client_log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ line }),
  }).catch(() => {
    /* 中间件未就绪时忽略 */
  });
}

function safeJson(v: unknown): string {
  try {
    const s = JSON.stringify(v);
    return s.length > 500 ? s.slice(0, 500) + "…" : s;
  } catch {
    return String(v);
  }
}
