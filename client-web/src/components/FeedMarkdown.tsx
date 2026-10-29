import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * 口播常是 LLM 吐的 Markdown。
 * 必须开 remark-gfm，否则 | 表会塌成一行纯文本（过绘 TopN 会很难看）。
 *
 * 表列序不固定（有时「排名|路径|…」，有时「物体|少画层|…」），
 * 样式按单元格内容分流，禁止用 nth-child 写死列宽（否则路径挤进 3em 叠字）。
 */
export function FeedMarkdown({ text }: { text: string }) {
  const src = normalizeMarkdownTables(humanizeSpokenMarkdown(text));
  if (!src.trim()) return null;
  return (
    <div className="feed-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h3 className="feed-md-h">{children}</h3>,
          h2: ({ children }) => <h3 className="feed-md-h">{children}</h3>,
          h3: ({ children }) => <h4 className="feed-md-h">{children}</h4>,
          p: ({ children }) => <p className="feed-md-p">{children}</p>,
          ul: ({ children }) => <ul className="feed-md-ul">{children}</ul>,
          ol: ({ children }) => <ol className="feed-md-ol">{children}</ol>,
          li: ({ children }) => <li className="feed-md-li">{children}</li>,
          code: ({ children }) => <code className="feed-md-code">{children}</code>,
          strong: ({ children }) => <strong>{children}</strong>,
          table: ({ children }) => (
            <div className="feed-md-table-wrap">
              <table className="feed-md-table">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead>{children}</thead>,
          tbody: ({ children }) => <tbody>{children}</tbody>,
          tr: ({ children }) => <tr>{children}</tr>,
          th: ({ children }) => (
            <th className={cellClass(children, true)}>{children}</th>
          ),
          td: ({ children }) => (
            <td className={cellClass(children, false)}>{children}</td>
          ),
        }}
      >
        {src}
      </ReactMarkdown>
    </div>
  );
}

function cellText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(cellText).join("");
  if (typeof node === "object" && "props" in node) {
    const props = (node as { props?: { children?: ReactNode } }).props;
    return cellText(props?.children);
  }
  return "";
}

/** 数字/短说明列收窄；路径/物体列加宽换行，避免叠到邻格。 */
function cellClass(children: ReactNode, isHeader: boolean): string {
  const t = cellText(children).trim();
  if (!t) return "feed-md-cell";
  if (isHeader) {
    if (/路径|物体|对象|名称|path/i.test(t)) return "feed-md-cell feed-md-cell-path";
    if (/排名|#|rank/i.test(t)) return "feed-md-cell feed-md-cell-rank";
    if (/说明|备注|note/i.test(t)) return "feed-md-cell feed-md-cell-note";
    return "feed-md-cell feed-md-cell-num";
  }
  // 纯数字 / 百分比 / 逗号千分位 / 短比分
  if (/^[\d,.\s/%+\-–—]+$/.test(t) && t.length <= 24) {
    return "feed-md-cell feed-md-cell-num";
  }
  if (/^\d{1,3}$/.test(t)) return "feed-md-cell feed-md-cell-rank";
  if (t.includes("/") || t.length > 18) return "feed-md-cell feed-md-cell-path";
  if (t.length <= 8) return "feed-md-cell feed-md-cell-note";
  return "feed-md-cell feed-md-cell-path";
}

/**
 * 模型常把整段口播糊成一行（`也不是山。##直接回答**不是。`）。
 * 在进 GFM 前补可读换行；有正常换行的文本尽量少动。
 *
 * 特别危险：`##直接回答可以…` 整段会被当成一级大标题（字又大又难读）；
 * `#0–#4` 这类 FD 序号也会被 GFM 认成标题。
 */
export function humanizeSpokenMarkdown(text: string): string {
  let s = text.replace(/\r\n/g, "\n").trim();
  if (!s) return s;

  // 开场双份兜底（流式+整包曾经叠过）
  {
    const m = s.match(/^(【开场】[\s\S]+?)(?=【开场】)/);
    if (m) {
      const first = m[1].trim();
      const rest = s.slice(m[0].length).trim();
      const compact = (x: string) => x.replace(/\s+/g, "");
      if (compact(rest).startsWith(compact(first).slice(0, 30))) {
        s = first;
      }
    }
  }

  // 已知小节：标题与正文拆开（##直接回答可以 → ## 直接回答\n\n可以）
  const section =
    "直接回答|依据|解读|现状|不建议|可选做法|方案[一二三123]";
  s = s.replace(
    new RegExp(`(#{1,3})\\s*(${section})(?=\\S)`, "g"),
    (_m, hashes: string, title: string) => `${hashes} ${title}\n\n`,
  );

  // ## / ### 标题前空行（单 # 不碰，避免 #12 事件号）
  s = s.replace(/([^\n])(#{2,3}\s*)/g, "$1\n\n$2");
  // ##标题 → ## 标题
  s = s.replace(/(^|\n)(#{2,3})([^\s#\n])/g, "$1$2 $3");

  // FD 事件号 #12 勿当 Markdown 标题
  s = s.replace(/(?<![`\\/#])#(\d+)/g, "`#$1`");

  // 粘连编号方案：「…冲突。2.用相机…3.分队列」→ 每条换行
  // 只认句读后的 1～9. + 汉字，避免动到 1.x / 2000.
  s = s.replace(
    /([。；！？\n])\s*([1-9])\.\s*(?=[\u4e00-\u9fff「『【*])/g,
    "$1\n\n$2. ",
  );
  // 段首「1.改远山」补空格，便于 GFM 认成有序列表
  s = s.replace(/(^|\n)([1-9])\.\s*(?=[\u4e00-\u9fff「『【*])/g, "$1$2. ");
  // 方案条内「预期收益 / 代价」另起一行，方便扫读
  s = s.replace(/([^\n])((?:预期)?收益[：:])/g, "$1\n$2");
  s = s.replace(/([^\n])(代价[：:])/g, "$1\n$2");

  // **一、 **二、 等小标题
  s = s.replace(/([^\n])(\*\*[一二三四五六七八九十]+[、.．])/g, "$1\n\n$2");
  // 句末后紧贴列表项：。-事件 / ：- **
  s = s.replace(/([。；：!?！？])(-(?!\s*-))/g, "$1\n$2");
  // 粘在正文后的「-事件/场景/过绘…」
  s = s.replace(
    /([^\n])(-\s*(?:事件|场景|过绘|遮挡|后处理|帧|Batches|CPU|GPU|工具|Gap|在Game|若确|若是|不要))/g,
    "$1\n$2",
  );
  // 列表项缺空格：-场景 → - 场景
  s = s.replace(/(^|\n)-([^\s\-\n])/g, "$1- $2");
  // 【标签】后若紧跟正文，换一行
  s = s.replace(/(【[^】]{1,12}】)(?=\S)/g, "$1\n");
  // 句号后跟加粗短标题 **直接回答**
  s = s.replace(/([。；])(\*\*[^*]{2,20}\*\*)/g, "$1\n\n$2");

  s = s.replace(/\n{3,}/g, "\n\n");
  return s.trim();
}

/**
 * 模型偶发把整张表挤在一行（…| 透明 | |---| | 1 |…）。
 * 把「| |」拆成换行，让 GFM 能认出表。
 */
function normalizeMarkdownTables(text: string): string {
  if (!text.includes("|")) return text;
  return text.replace(/\|\s*\|/g, "|\n|");
}
