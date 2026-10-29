/** RDC 文件转换面板：独立于设备连接和大模型会话。 */
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Input, Space, Spin, Tag, Tabs, Typography } from "antd";
import {
  fetchRdcData, fetchRdcEnvironment, fetchRdcHistory, fetchRdcJob,
  importRdc, uploadRdc, rdcFileUrl, type RdcJob, type RdcData,
} from "../services/renderdocService";

const BUSY = new Set(["importing", "queued", "opening", "replaying", "extracting", "summarizing"]);
export function RenderDocPanel({ active }: { active: boolean }) {
  const { t } = useTranslation();
  const [path, setPath] = useState("");
  const [host, setHost] = useState("");
  const [history, setHistory] = useState<RdcJob[]>([]);
  const [job, setJob] = useState<RdcJob | null>(null);
  const [data, setData] = useState<RdcData | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [env, setEnv] = useState<{available: boolean; error?: string; directory?: string} | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const selection = useRef("");
  const mounted = useRef(false);
  async function refresh() {
    try {
      const response = await fetchRdcHistory();
      if (mounted.current) setHistory(response.items);
    } catch (e) { if (mounted.current) setError(String(e)); }
  }
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; selection.current = ""; };
  }, []);
  useEffect(() => {
    if (!active) return;
    void refresh();
    void fetchRdcEnvironment().then(v => { if (mounted.current) setEnv(v); })
      .catch(e => { if (mounted.current) setError(String(e)); });
  }, [active]);
  async function choose(item: RdcJob) {
    selection.current = item.id;
    setJob(item); setData(null); setError("");
    if (item.status === "done" || item.status === "partial") {
      try {
        const value = await fetchRdcData(item.id);
        if (mounted.current && selection.current === item.id) setData(value);
      } catch (e) { if (mounted.current && selection.current === item.id) setError(String(e)); }
    }
  }
  useEffect(() => {
    if (!active || !job || !BUSY.has(job.status)) return;
    let cancelled = false;
    const id = job.id;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await fetchRdcJob(id);
        if (cancelled || selection.current !== id) return;
        setJob(next);
        if (BUSY.has(next.status)) timer = setTimeout(() => { void poll(); }, 1500);
        else {
          await refresh();
          if ((next.status === "done" || next.status === "partial") && !cancelled) {
            const value = await fetchRdcData(id);
            if (!cancelled && selection.current === id) setData(value);
          }
        }
      } catch (e) { if (!cancelled) setError(String(e)); }
    };
    timer = setTimeout(() => { void poll(); }, 1000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [active, job?.id, job?.status]);
  async function start(file?: File) {
    setBusy(true); setError("");
    try {
      const created = file ? await uploadRdc(file, host) : await importRdc(path, host);
      if (!mounted.current) return;
      await choose(created); await refresh();
    } catch (e) { if (mounted.current) setError(String(e)); }
    finally { if (mounted.current) setBusy(false); }
  }
  const jsonView = (value: unknown) => <pre className="rdc-json">{JSON.stringify(value, null, 2)}</pre>;
  return (
    <div className="rdc-panel">
      <div className="rdc-toolbar">
        <Input value={path} onChange={e => setPath(e.target.value)} placeholder={t("renderdoc.path")}
          aria-label={t("renderdoc.path")} style={{ flex: 1, minWidth: 200 }} />
        <Button disabled={!path.trim() || busy || env?.available === false} loading={busy} onClick={() => void start()}>
          {t("renderdoc.import")}
        </Button>
        <Button disabled={busy || env?.available === false} onClick={() => input.current?.click()}>
          {t("renderdoc.upload")}
        </Button>
        <input ref={input} type="file" accept=".rdc" hidden onChange={e => {
          const file = e.target.files?.[0]; e.target.value = ""; if (file) void start(file);
        }} />
        <Input value={host} onChange={e => setHost(e.target.value)} placeholder={t("renderdoc.remoteHost")}
          aria-label={t("renderdoc.remoteHost")} style={{ width: 230 }} />
        {env ? <Tag color={env.available ? "green" : "orange"}>{t(env.available ? "renderdoc.ready" : "renderdoc.missing")}</Tag> : null}
      </div>
      {env?.error ? <Alert type="warning" title={env.error} /> : null}
      {error ? <Alert type="error" title={error} /> : null}
      <div className="rdc-layout">
        <aside className="rdc-history">
          <Space><Typography.Text>{t("renderdoc.history")}</Typography.Text><Button size="small" onClick={() => void refresh()}>{t("renderdoc.refresh")}</Button></Space>
          {history.map(item => <button key={item.id} className="rdc-history-item" data-active={item.id === job?.id}
            onClick={() => void choose(item)}>
            <strong>{item.name}</strong><small>{new Date(item.created_at).toLocaleString()}</small>
            <span>{t("renderdoc.status." + item.status, { defaultValue: item.status })}</span>
          </button>)}
          {!history.length ? <p>{t("renderdoc.empty")}</p> : null}
        </aside>
        <main className="rdc-content">
          {!job ? <Typography.Paragraph type="secondary">{t("renderdoc.hint")}</Typography.Paragraph> : <>
            <Space wrap>
              <Typography.Text strong>{job.name}</Typography.Text>
              <Tag>{t("renderdoc.status." + job.status, { defaultValue: job.status })}</Tag>
              {BUSY.has(job.status) ? <Spin size="small" /> : null}
              {data ? <><Button href={rdcFileUrl(job.id, "capture.aidata")}>{t("renderdoc.downloadAi")}</Button>
                <Button href={rdcFileUrl(job.id, "capture.detaildata")}>{t("renderdoc.downloadDetail")}</Button></> : null}
            </Space>
            {job.error ? <Alert type="error" title={job.error} /> : null}
            {data ? <>
              {data.limitations.map((line, i) => <Typography.Paragraph key={i} type="secondary" style={{ margin: "4px 0" }}>{line}</Typography.Paragraph>)}
              <Tabs items={[
                {key:"overview",label:t("renderdoc.overview"),children:<>
                  {data.images.map(img => <figure key={img.file}><img className="rdc-preview"
                    src={rdcFileUrl(job.id,img.file)} alt={t("renderdoc.image."+img.kind, { defaultValue: img.kind })} />
                    <figcaption>{t("renderdoc.image."+img.kind, { defaultValue: img.kind })}{img.event_id != null ? " · EID "+img.event_id : ""}</figcaption></figure>)}
                  {jsonView({capture:data.capture,overview:data.overview,capabilities:data.capabilities,coverage:data.coverage})}</>},
                {key:"stages",label:t("renderdoc.stages"),children:jsonView(data.stages)},
                {key:"shaders",label:t("renderdoc.shaders"),children:jsonView(data.shader_usage)},
                {key:"resources",label:t("renderdoc.resources"),children:jsonView(data.largest_resources)},
                {key:"draws",label:t("renderdoc.draws"),children:jsonView(data.largest_geometry_draws)},
                {key:"aidata",label:t("renderdoc.aidata"),children:jsonView(data)},
              ]} />
            </> : null}
          </>}
        </main>
      </div>
    </div>
  );
}
