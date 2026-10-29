/** RenderDoc 接口与类型；组件不直接发起网络请求。 */
import { API_BASE } from "./types";

export type RdcJob = {
  id: string; name: string; status: string; error: string; created_at: string;
  overview: Record<string, number | null> | null;
  capabilities: Record<string, boolean> | null; warnings: string[];
};
export type RdcData = {
  schema: string; source: Record<string, unknown>; capture: Record<string, unknown>;
  capabilities: Record<string, boolean>; overview: Record<string, number | null>;
  coverage: Record<string, unknown>; stages: unknown[]; shader_usage: unknown[];
  largest_resources: unknown[]; largest_geometry_draws: unknown[];
  images: { file: string; kind: string; event_id: number | null }[];
  limitations: string[];
};
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API_BASE + "/mcp/renderdoc" + path, {
    ...init, cache: "no-store", signal: AbortSignal.timeout(120000),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(body.detail || String(response.status));
  }
  return response.json() as Promise<T>;
}
export const fetchRdcEnvironment = () => request<{ available: boolean; error?: string; directory?: string }>("/environment");
export const fetchRdcHistory = () => request<{items: RdcJob[]}>("/history");
export const fetchRdcJob = (id: string) => request<RdcJob>("/" + encodeURIComponent(id));
export const fetchRdcData = (id: string) => request<RdcData>("/" + encodeURIComponent(id) + "/data");
export const importRdc = (path: string, remote_host: string) => request<RdcJob>("/import", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path, remote_host }),
});
export const uploadRdc = (file: File, remote_host: string) => request<RdcJob>(
  "/upload?" + new URLSearchParams({ name: file.name, remote_host }), {
    method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file,
  });
export const rdcFileUrl = (id: string, name: string) =>
  API_BASE + "/mcp/renderdoc/" + encodeURIComponent(id) + "/files/" + encodeURIComponent(name);
