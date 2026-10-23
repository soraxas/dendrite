// Fan-out groups (`step.named(key)` copies such as cut_clip[0..n]) and the edges for what the
// pipeline body decided from reading a result. Pure functions over the DAG, so they're testable.
import type { DagEdge, DagNode, PipelineRead } from "./api";

/** Groups with at least this many items start collapsed. */
export const COLLAPSE_AT = 4;

export interface FanoutGroup {
  id: string; // "group:<step>"
  step: string;
  members: DagNode[];
}

export const groupId = (step: string) => `group:${step}`;

/** Steps called more than once under keys, in first-call order. */
export function fanoutGroups(nodes: DagNode[]): FanoutGroup[] {
  const by = new Map<string, DagNode[]>();
  for (const n of nodes) {
    if (n.kind !== "node" || n.key == null || !n.step) continue;
    by.set(n.step, [...(by.get(n.step) ?? []), n]);
  }
  return [...by].filter(([, m]) => m.length > 1).map(([step, members]) => ({ id: groupId(step), step, members }));
}

/** Which groups are collapsed: the default (big groups) flipped by the user's toggles. */
export function collapsedSteps(groups: FanoutGroup[], toggled: Set<string>): Set<string> {
  return new Set(groups.filter((g) => (g.members.length >= COLLAPSE_AT) !== toggled.has(g.step)).map((g) => g.step));
}

export interface GroupSummary {
  status: string;
  line2: string;
  line3: string;
  done: number;
  total: number;
  /** Items finished plus the reported progress of running ones, as a fraction of all items. */
  fraction: number;
}

const ACTIVE = ["running", "waiting"];

/** One status for a group: failures first, then anything active, then queued, else finished. */
export function summarize(members: DagNode[]): GroupSummary {
  const count = (s: string[]) => members.filter((m) => s.includes(m.status)).length;
  const failed = count(["failed"]), active = count(ACTIVE), queued = count(["queued"]);
  const ran = count(["computed"]), reused = count(["cached"]), frozen = count(["pinned"]);
  const total = members.length;
  const done = ran + reused + frozen;
  const status = failed ? "failed" : active ? "running" : queued ? "queued" : ran ? "computed" : reused ? "cached" : frozen ? "pinned" : "queued";
  const parts = [ran && `ran ${ran}`, reused && `reused ${reused}`, frozen && `frozen ${frozen}`, active && `${active} running`, queued && `${queued} queued`, failed && `${failed} failed`].filter(Boolean);
  const took = members.reduce((s, m) => s + (m.status === "computed" ? m.duration ?? 0 : 0), 0);
  const partial = members.reduce((s, m) => (m.status === "running" && m.progress?.total ? s + Math.min(1, m.progress.done / m.progress.total) : s), 0);
  const fraction = total ? (done + partial) / total : 0;
  const pct = active && partial ? ` · ${Math.floor(fraction * 100)}%` : "";
  const line2 = done === total ? `${parts.join(" · ")}` : `${done} of ${total} done · ${parts.join(" · ")}${pct}`;
  return { status, line2: line2.charAt(0).toUpperCase() + line2.slice(1), line3: took ? `took ${took.toFixed(1)}s in total` : "", done, total, fraction };
}

/** Map each member id to its group id when that group is collapsed. */
export function memberMap(groups: FanoutGroup[], collapsed: Set<string>): Map<string, string> {
  const m = new Map<string, string>();
  for (const g of groups) if (collapsed.has(g.step)) for (const n of g.members) m.set(n.id, g.id);
  return m;
}

/** Edges with collapsed members redirected to their group (duplicates and self-loops dropped by the caller's merge). */
export function collapseEdges(edges: DagEdge[], members: Map<string, string>): DagEdge[] {
  const at = (id: string) => members.get(id) ?? id;
  return edges.map((e) => ({ ...e, from: at(e.from), to: at(e.to) })).filter((e) => e.from !== e.to);
}

export interface ControlEdge {
  from: string;
  to: string;
  label: string;
}

/**
 * Dashed "decides" edges: from a step whose result the pipeline body read, to the first steps the
 * body called after reading it (those not already fed by another step called after the read).
 * Reads of experiment inputs (configs) don't get edges; they'd connect to everything.
 */
export function controlEdges(nodes: DagNode[], edges: DagEdge[], reads: PipelineRead[], members: Map<string, string>): ControlEdge[] {
  const seq = new Map(nodes.filter((n) => n.kind === "node").map((n) => [n.id, n.seq ?? 0]));
  const at = (id: string) => members.get(id) ?? id;
  const out = new Map<string, ControlEdge>();
  for (const r of reads) {
    if (!r.producer || r.producer.startsWith("input:") || !seq.has(r.producer)) continue;
    const later = (id: string) => (seq.get(id) ?? -1) >= r.after;
    for (const [id] of seq) {
      // A queued step's inputs aren't known until it starts, so it can't be told apart from an unfed one yet.
      if (!later(id) || nodes.find((n) => n.id === id)?.status === "queued") continue;
      const fedByLater = edges.some((e) => e.to === id && later(e.from));
      if (fedByLater) continue;
      const from = at(r.producer), to = at(id);
      if (from !== to) out.set(`${from}~>${to}`, { from, to, label: "decides" });
    }
  }
  return [...out.values()];
}

/** Inputs the body read itself: they configure the run rather than feed one step. */
export function configInputs(nodes: DagNode[], reads: PipelineRead[]): Set<string> {
  const ids = new Set(nodes.filter((n) => n.kind === "input").map((n) => n.id));
  return new Set(reads.map((r) => r.producer).filter((p): p is string => !!p && ids.has(p)));
}

/** The selection to show after a click: clicking the selected item again clears it. */
export function nextSelection(current: string | undefined, clicked: string): string | undefined {
  return current === clicked ? undefined : clicked;
}

/** A draft's group: how many items would run, be reused or stay frozen. */
export function summarizePlan(members: { plan?: string }[]): GroupSummary {
  const n = (o: string) => members.filter((m) => (m.plan ?? "run") === o).length;
  const run = n("run"), reuse = n("reuse"), frozen = n("frozen");
  const status = run ? "planrun" : reuse ? "cached" : "pinned";
  const parts = [run && `will run ${run}`, reuse && `reused ${reuse}`, frozen && `frozen ${frozen}`].filter(Boolean) as string[];
  const line2 = parts.join(" · ");
  return { status, line2: line2.charAt(0).toUpperCase() + line2.slice(1), line3: "", done: reuse + frozen, total: members.length, fraction: 0 };
}

/** "Running · 40/85 frames · ~6s left" for a step that reports progress. */
export function progressLine(p: { done: number; total: number | null; unit: string | null }, elapsed: number | null): { text: string; fraction?: number } {
  const unit = p.unit ? ` ${p.unit}` : "";
  if (p.total == null || p.total <= 0) return { text: `${fmtCount(p.done)}${unit}` };
  const fraction = Math.max(0, Math.min(1, p.done / p.total));
  let left = "";
  if (elapsed != null && p.done > 0 && fraction < 1) {
    const s = (elapsed / p.done) * (p.total - p.done);
    left = ` · ~${s < 60 ? `${Math.ceil(s)}s` : `${Math.ceil(s / 60)}m`} left`;
  }
  return { text: `${fmtCount(p.done)}/${fmtCount(p.total)}${unit}${left}`, fraction };
}

const fmtCount = (x: number) => (Number.isInteger(x) ? String(x) : x.toFixed(1));
