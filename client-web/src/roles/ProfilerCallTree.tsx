/** 单帧采样调用关系按需展示；不参与性能判定或模型编排。 */
import { createContext, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { fetchAndroidProfilerCallTree } from "../services/androidService";
import type { ProfilerCallNode, ProfilerCallTree } from "../services/types";

type TreeIndex = { tree: ProfilerCallTree; children: Map<number, number[]>; names: Map<string, number[]> };
type CallsContext = { available: boolean; index: TreeIndex | null; error: boolean; load: () => void };
const Calls = createContext<CallsContext | null>(null);
const PAGE_SIZE = 20;

export function ProfilerCallTreeProvider({ name, threadId, frame, available, children }: {
  name: string; threadId: string; frame: number; available: boolean; children: ReactNode;
}) {
  const [tree, setTree] = useState<ProfilerCallTree | null>(null);
  const [error, setError] = useState(false);
  const pending = useRef(false);
  const index = useMemo(() => {
    if (!tree) return null;
    const children = new Map<number, number[]>();
    const names = new Map<string, number[]>();
    tree.nodes.forEach((node, id) => {
      const siblings = children.get(node.parent) || [];
      siblings.push(id);
      children.set(node.parent, siblings);
      const matches = names.get(node.name) || [];
      matches.push(id);
      names.set(node.name, matches);
    });
    return { tree, children, names };
  }, [tree]);
  function load() {
    if (!available || tree || pending.current) return;
    pending.current = true;
    setError(false);
    void fetchAndroidProfilerCallTree(name, threadId, frame).then(setTree).catch(() => {
      setError(true);
    }).finally(() => { pending.current = false; });
  }
  return <Calls.Provider value={{ available, index, error, load }}>{children}</Calls.Provider>;
}

function CallMetrics({ node }: { node: ProfilerCallNode }) {
  const { t } = useTranslation();
  const bytes = node.bytes < 1024 ? `${node.bytes} B` : `${(node.bytes / 1024).toFixed(1)} KB`;
  return <span className="profiler-call-metrics">{t("device.profilerCallsMetrics", {
    hits: node.hits, total: node.total_ms.toFixed(3), self: node.self_ms.toFixed(3), bytes,
  })}</span>;
}

// 子节点打开后才挂载，避免 Deep Profile 深树在收起状态创建完整 DOM。
function CallBranch({ id, index }: { id: number; index: TreeIndex }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const node = index.tree.nodes[id];
  const children = index.children.get(id) || [];
  return <details className="profiler-call-branch" onToggle={e => {
    if (e.target === e.currentTarget) setOpen(e.currentTarget.open);
  }}>
    <summary><span>{node.name}</span><CallMetrics node={node} /></summary>
    {open ? children.length ? <CallChildren ids={children} index={index} />
      : <p className="profiler-aidata-hint">{t("device.profilerCallsLeaf")}</p> : null}
  </details>;
}
function CallChildren({ ids, index }: { ids: number[]; index: TreeIndex }) {
  const { t } = useTranslation();
  const [count, setCount] = useState(PAGE_SIZE);
  return <div className="profiler-call-children">
    {ids.slice(0, count).map(id => <CallBranch key={id} id={id} index={index} />)}
    {count < ids.length ? <button type="button" onClick={() => setCount(count + PAGE_SIZE)}>
      {t("device.profilerCallsMore", { n: Math.min(PAGE_SIZE, ids.length - count) })}
    </button> : null}
  </div>;
}
function CallPath({ id, index }: { id: number; index: TreeIndex }) {
  const { t } = useTranslation();
  const ancestors: number[] = [];
  let parent = index.tree.nodes[id].parent;
  // 平表先父后子；防止异常历史数据形成环。
  while (parent >= 0 && parent < id && ancestors.length < index.tree.nodes.length) {
    ancestors.push(parent);
    const next = index.tree.nodes[parent].parent;
    if (next >= parent) break;
    parent = next;
  }
  ancestors.reverse();
  const node = index.tree.nodes[id];
  const children = index.children.get(id) || [];
  return <section className="profiler-call-path">
    <div className="profiler-aidata-hint">{t("device.profilerCallsPath")}</div>
    <ol className="profiler-call-ancestors">
      {ancestors.map(ancestor => <li key={ancestor}>{index.tree.nodes[ancestor].name}</li>)}
      <li><strong>{node.name}</strong></li>
    </ol>
    <CallMetrics node={node} />
    <div className="profiler-aidata-hint">{t("device.profilerCallsChildren", { n: children.length })}</div>
    {children.length ? <CallChildren ids={children} index={index} />
      : <p className="profiler-aidata-hint">{t("device.profilerCallsLeaf")}</p>}
  </section>;
}
function CallPaths({ name, index }: { name: string; index: TreeIndex }) {
  const { t } = useTranslation();
  const [count, setCount] = useState(PAGE_SIZE);
  const ids = index.names.get(name) || [];
  return <>
    <p className="profiler-aidata-hint">{t("device.profilerCallsHint")}</p>
    <p>{t("device.profilerCallsPathCount", { n: ids.length })}</p>
    {!ids.length ? <p>{t("device.profilerCallsNotFound")}</p> : null}
    {ids.slice(0, count).map(id => <CallPath key={id} id={id} index={index} />)}
    {count < ids.length ? <button type="button" onClick={() => setCount(count + PAGE_SIZE)}>
      {t("device.profilerCallsMore", { n: Math.min(PAGE_SIZE, ids.length - count) })}
    </button> : null}
  </>;
}
export function ProfilerMarkerCalls({ name, value }: { name: string; value: string }) {
  const { t } = useTranslation();
  const context = useContext(Calls);
  const [open, setOpen] = useState(false);
  const summary = <><span className="profiler-aidata-name" title={name}>{name}</span>
    <span className="profiler-aidata-ms">{value}</span></>;
  if (!context) return <div className="profiler-marker-summary">{summary}</div>;
  return <details className="profiler-marker-calls" onToggle={e => {
    if (e.target !== e.currentTarget) return;
    setOpen(e.currentTarget.open);
    if (e.currentTarget.open) context.load();
  }}>
    <summary aria-label={`${t("device.profilerCallsExpand")}: ${name}`}>
      <span className="profiler-marker-summary">{summary}</span>
    </summary>
    {open ? <div className="profiler-call-content">
      {!context.available ? <p>{t("device.profilerCallsMissing")}</p>
        : context.index ? <CallPaths name={name} index={context.index} />
        : context.error ? <p>{t("device.profilerCallsError")} <button type="button" onClick={context.load}>
          {t("device.profilerCallsRetry")}</button></p>
        : <p role="status">{t("device.profilerCallsLoading")}</p>}
    </div> : null}
  </details>;
}
