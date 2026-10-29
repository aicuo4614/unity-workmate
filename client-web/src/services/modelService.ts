/** 模型配置接口：只在提交时发送 Key，不持久化到浏览器。 */
import { API_BASE } from "./types";

export type ModelStatus = {
  provider: string;
  configured: boolean;
  base_url: string;
  model: string;
  request_timeout: number;
};
export type ModelInput = Pick<ModelStatus, "base_url" | "model" | "request_timeout"> & { api_key?: string };

async function request<T>(path = "", method = "GET", body?: ModelInput): Promise<T> {
  const response = await fetch(`${API_BASE}/settings/model${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (!response.ok) throw new Error("modelSettingsRequestFailed");
  return response.json();
}
export const fetchModelConfig = () => request<ModelStatus>();
export const saveModelConfig = (input: ModelInput) => request<ModelStatus>("", "PUT", input);
export const testModelConfig = (input: ModelInput) => request<{ ok: boolean }>("/test", "POST", input);
