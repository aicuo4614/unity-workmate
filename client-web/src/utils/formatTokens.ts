/** 本局 token 展示：≥1000 用 k（1.2k / 15k）。 */
export function formatTokenCount(n: number): string {
  const v = Math.max(0, Math.floor(Number(n) || 0));
  if (v < 1000) return String(v);
  const k = v / 1000;
  if (k < 10) {
    const s = k.toFixed(1);
    return `${s.endsWith(".0") ? s.slice(0, -2) : s}k`;
  }
  if (k < 100) {
    const s = k.toFixed(1);
    return `${s.endsWith(".0") ? s.slice(0, -2) : s}k`;
  }
  return `${Math.round(k)}k`;
}
