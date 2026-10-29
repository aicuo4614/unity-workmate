import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vite";

const rootDir = path.dirname(fileURLToPath(import.meta.url));
const clientLogDir = path.join(rootDir, "logs");
const clientLogFile = path.join(clientLogDir, "app.log");

/** 开发态接收浏览器 POST，写入 client-web/logs/app.log（与 server/logs 分离）。 */
function clientLogPlugin(): Plugin {
  return {
    name: "ailearning-client-log",
    configureServer(server) {
      server.middlewares.use("/__client_log", (req, res, next) => {
        if (req.method !== "POST") {
          next();
          return;
        }
        const chunks: Buffer[] = [];
        req.on("data", (c) => chunks.push(c));
        req.on("end", () => {
          try {
            const raw = Buffer.concat(chunks).toString("utf8");
            const body = JSON.parse(raw || "{}") as { line?: string };
            const line = String(body.line || "").trim();
            if (line) {
              fs.mkdirSync(clientLogDir, { recursive: true });
              const stamp = new Date().toISOString().slice(11, 19);
              fs.appendFileSync(clientLogFile, `${stamp} ${line}\n`, "utf8");
            }
            res.statusCode = 204;
            res.end();
          } catch {
            res.statusCode = 400;
            res.end("bad log");
          }
        });
      });
    },
  };
}

// 开发时把 /health /chat /mcp 代理到 FastAPI，避免 CORS
export default defineConfig({
  plugins: [react(), clientLogPlugin()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/health": "http://127.0.0.1:8000",
      "/settings": "http://127.0.0.1:8000",
      "/chat": "http://127.0.0.1:8000",
      "/mcp": "http://127.0.0.1:8000",
    },
  },
});
