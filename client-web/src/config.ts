/**
 * 前端运行配置（与 server 落盘无关）。
 * mode 默认跟 Vite：开发=development，build=production。
 */
export type AppMode = "development" | "production";

const mode: AppMode =
  import.meta.env.MODE === "production" ? "production" : "development";

export const appConfig = {
  mode,
  /** 空字符串 = 走 Vite proxy（/health /chat /mcp） */
  apiBase: (import.meta.env.VITE_API_BASE as string | undefined) || "",
  /** 开发态：写 client-web/logs（经 Vite 中间件）并打 console */
  clientLogEnabled: mode === "development",
} as const;

export const isDev = appConfig.mode === "development";
