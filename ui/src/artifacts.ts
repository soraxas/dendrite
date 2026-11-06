// Rows for an experiment's Artifacts tab: every input and step output, grouped stage → step.
import type { ArtifactRef, Dag, DagNode } from "./api";
import { artifactCategory, type Category } from "./components/ArtifactIcon";

export type Source = "input" | "computed" | "reused" | "pinned" | "pending";

export interface ArtifactRow {
  /** Stable row id: ``input:<name>`` or ``<node>.<output>``. */
  id: string;
  stage: string;
  /** The step (a fan-out item's step, without its key) or "inputs". */
  step: string;
  node: string | null;
  item: string | null;
  output: string;
  ref: ArtifactRef;
  source: Source;
  /** The experiment whose run produced it, when this one reused it. */
  from: string | null;
  names: string[];
  kind: FilterKind;
}

export const FILTER_KINDS = ["image", "video", "3d", "directory", "value", "other"] as const;
export type FilterKind = (typeof FILTER_KINDS)[number];
export const KIND_LABEL: Record<FilterKind, string> = {
  image: "Images",
  video: "Video",
  "3d": "3D",
  directory: "Folders",
  value: "Values",
  other: "Other",
};

export function filterKind(c: Category): FilterKind {
  if (c === "image") return "image";
  if (c === "video") return "video";
  if (c === "model") return "3d";
  if (c === "folder") return "directory";
  if (c === "value") return "value";
  return "other";
}

const stageName = (n: DagNode) => {
  const s = n.stage?.[0];
  return typeof s === "string" ? s : (s?.name ?? "");
};

function source(n: DagNode, self: string): Source {
  if (n.status === "pinned") return "pinned";
  if (n.status === "cached" || (n.execution_experiment && n.execution_experiment !== self)) return "reused";
  if (n.status === "computed") return "computed";
  return "pending";
}

/** Inputs first, then every step's outputs in pipeline order. Steps without outputs yet are left out. */
export function artifactRows(dag: Dag): ArtifactRow[] {
  const self = dag.experiment.id;
  const names = dag.names ?? {};
  const rows: ArtifactRow[] = [];
  for (const n of dag.nodes) {
    if (n.kind !== "input" || !n.artifact) continue;
    rows.push({
      id: `input:${n.name}`,
      stage: "",
      step: "inputs",
      node: null,
      item: null,
      output: n.name,
      ref: n.artifact,
      source: "input",
      from: null,
      names: names[n.artifact.hash] ?? [],
      kind: filterKind(artifactCategory(n.artifact)),
    });
  }
  const steps = dag.nodes.filter((n) => n.kind === "node").sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
  for (const n of steps) {
    const src = source(n, self);
    for (const [out, ref] of Object.entries(n.outputs ?? {})) {
      if (!ref || (ref.kind as string) === "pending") continue;
      rows.push({
        id: `${n.name}.${out}`,
        stage: stageName(n),
        step: n.step ?? n.name,
        node: n.name,
        item: n.key ?? null,
        output: out,
        ref,
        source: src,
        from: src === "reused" ? (n.execution_experiment ?? null) : null,
        names: names[ref.hash] ?? [],
        kind: filterKind(artifactCategory(ref)),
      });
    }
  }
  return rows;
}

export function filterRows(rows: ArtifactRow[], kind?: string, q?: string): ArtifactRow[] {
  const needle = (q ?? "").trim().toLowerCase();
  return rows.filter(
    (r) =>
      (!kind || r.kind === kind) &&
      (!needle ||
        [r.step, r.node ?? "", r.output, r.ref.filename ?? "", ...r.names].some((v) =>
          v.toLowerCase().includes(needle),
        )),
  );
}

export interface StepGroup {
  step: string;
  rows: ArtifactRow[];
  /** Fan-out items in this step (0 for a plain step). */
  items: number;
}
export interface StageGroup {
  stage: string;
  steps: StepGroup[];
}

/** Group rows by stage, then step, keeping first-seen (pipeline) order. */
export function groupRows(rows: ArtifactRow[]): StageGroup[] {
  const stages = new Map<string, Map<string, ArtifactRow[]>>();
  for (const r of rows) {
    const st = stages.get(r.stage) ?? new Map<string, ArtifactRow[]>();
    stages.set(r.stage, st);
    st.set(r.step, [...(st.get(r.step) ?? []), r]);
  }
  return [...stages].map(([stage, steps]) => ({
    stage,
    steps: [...steps].map(([step, rs]) => ({
      step,
      rows: rs,
      items: new Set(rs.filter((r) => r.item != null).map((r) => r.item)).size,
    })),
  }));
}

/** Experiments to compare one output across: this one, its parent and its direct forks. */
export function compareCandidates(
  tree: { id: string; parent_id: string | null }[],
  base: string,
  limit = 8,
): { ids: string[]; more: number } {
  const me = tree.find((e) => e.id === base);
  const ids = [base];
  if (me?.parent_id && tree.some((e) => e.id === me.parent_id)) ids.push(me.parent_id);
  const forks = tree.filter((e) => e.parent_id === base).map((e) => e.id);
  const all = [...ids, ...forks];
  return { ids: all.slice(0, limit), more: Math.max(0, all.length - limit) };
}
