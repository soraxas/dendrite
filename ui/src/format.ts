export const short = (h?: string | null, n = 10) => (h ? h.slice(0, n) : "");

export function fmtDur(s?: number | null): string {
  if (s == null || Number.isNaN(s)) return "";
  if (s < 1) return `${(s * 1000).toFixed(0)}ms`;
  if (s < 120) return `${s.toFixed(1)}s`;
  return `${(s / 60).toFixed(1)}m`;
}

export function fmtSize(b?: number | null): string {
  if (b == null) return "";
  if (b < 1024) return `${b} B`;
  if (b < 1 << 20) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1 << 30) return `${(b / (1 << 20)).toFixed(1)} MB`;
  return `${(b / (1 << 30)).toFixed(2)} GB`;
}

export const fmtTime = (t?: number | null) => (t ? new Date(t * 1000).toLocaleString() : "");

export function fmtVal(v: unknown): string {
  if (v === undefined || v === null) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(5))); // 0.001, not 0.0010000
  return typeof v === "string" ? JSON.stringify(v) : JSON.stringify(v);
}

export function flatten(
  obj: Record<string, unknown> | null | undefined,
  prefix = "",
  out: Record<string, unknown> = {},
) {
  for (const [k, v] of Object.entries(obj ?? {})) {
    if (v && typeof v === "object" && !Array.isArray(v) && Object.keys(v).length)
      flatten(v as Record<string, unknown>, `${prefix}${k}.`, out);
    else out[`${prefix}${k}`] = v;
  }
  return out;
}

/** "input video · produced by extract · read by colmap, train" */
export function describeRoles(roles: { kind: string; node: string | null; name: string }[]): string {
  const parts: string[] = [];
  const inputs = roles.filter((r) => r.kind === "input").map((r) => r.name);
  if (inputs.length) parts.push(`input ${inputs.join(", ")}`);
  for (const [kind, verb] of [
    ["produced", "produced by"],
    ["read", "read by"],
  ] as const) {
    const nodes = [...new Set(roles.filter((r) => r.kind === kind).map((r) => r.node))];
    if (nodes.length) parts.push(`${verb} ${nodes.join(", ")}`);
  }
  return parts.join(" · ");
}

/** "just now", "5m ago", "3h ago", "2d ago", then a date. */
export function fmtAgo(t?: number | null, now = Date.now() / 1000): string {
  if (!t) return "";
  const s = Math.max(0, now - t);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 7 * 86400) return `${Math.round(s / 86400)}d ago`;
  return new Date(t * 1000).toLocaleDateString();
}
