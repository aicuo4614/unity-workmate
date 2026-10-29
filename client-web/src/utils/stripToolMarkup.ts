/**
 * 剥掉模型误写入口播的伪工具调用标记（与 server/graph/spoken_text.py 对齐）。
 * 跨 chunk 拼接后也要再剥一遍，否则 calls>invokename= 会粘在上一段中文后面。
 */
export function stripToolMarkup(text: string): string {
  if (!text) return "";
  let t = text.replace(/\uFF5C/g, "|").replace(/\uFF1C/g, "<").replace(/\uFF1E/g, ">");
  // 从伪工具起点砍到行末
  t = t.replace(
    /(?:(?:\|\|{0,1}(?:DSML\|)?)?(?:calls|invoke|parameter|function)\s*>|(?:invokename|parametername)\s*=|invoke\s*name\s*=|parameter\s*name\s*=|<[^>\n]*(?:DSML|invoke|parameter|function)[^>\n]*>).*$/gim,
    "",
  );
  // 残留碎片
  t = t.replace(
    /(?:\|\|{0,1}(?:DSML\|)?[a-z_][\w]*\s*(?:=\"[^\"]*\"|[=>][^|]*)?|\b(?:invokename|parametername)\s*=\s*\"[^\"]*\"|\bstring\s*=\s*\"[^\"]*\"|\b(?:calls|invoke|parameter|function)\s*>)/gi,
    "",
  );
  return t
    .split("\n")
    .filter((line) => {
      const s = line.trim();
      if (!s) return true;
      const low = s.toLowerCase();
      if (
        low.includes("dsml") ||
        low.includes("invokename=") ||
        low.includes("parametername=") ||
        low.includes("calls>") ||
        low.includes('string="false"')
      ) {
        return false;
      }
      if (s.startsWith("<") || s.startsWith("|")) return false;
      return true;
    })
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function hasToolMarkup(text: string): boolean {
  if (!text) return false;
  const t = text.replace(/\uFF5C/g, "|").toLowerCase();
  return (
    t.includes("dsml") ||
    t.includes("invokename=") ||
    t.includes("parametername=") ||
    t.includes("invoke name=") ||
    t.includes("calls>") ||
    t.includes('string="false"')
  );
}
