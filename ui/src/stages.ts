// Stages (`dendrite.stage` in code, or assigned in the UI's pipeline view) as collapsible cards:
// the pipeline's high-level picture. Pure functions over the DAG, so they're testable.
import type { DagNode, PipelineView, StageInfo } from "./api";
import { fanoutGroups, groupId, moreId, sampleItems, type FanoutGroup } from "./fanout";

export const STAGE_COLORS = ["teal", "blue", "purple", "orange", "green", "pink", "red", "gray"] as const;
export const EMPTY_VIEW: PipelineView = { assign: {}, styles: {} };

export interface StageGroup {
  id: string; // "stage:<a>/<b>"
  path: string[];
  name: string; // label (view style) or the code name
  description: string | null;
  color: string;
  members: DagNode[]; // every step inside, nested stages included
  fromView: boolean; // defined only by the UI view, not by code
}

export const stageId = (path: string[]) => `stage:${path.join("/")}`;

/** A node's stage path: from code, else from the view's assignment of its step. */
export function stagePath(n: DagNode, view: PipelineView): StageInfo[] {
  const code = (n.stage ?? []).map((s) => (typeof s === "string" ? { name: s } : s));
  if (code.length) return code;
  const assigned = view.assign[n.step ?? n.name];
  return assigned ? [{ name: assigned }] : [];
}

/** A stage's colour: the view's, then the code's, then a stable pick from its name. */
export function stageColor(name: string, view: PipelineView, code?: string | null): string {
  const c = view.styles[name]?.color ?? code;
  if (c && (STAGE_COLORS as readonly string[]).includes(c)) return c;
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return STAGE_COLORS[h % (STAGE_COLORS.length - 1)]; // gray only when asked for
}

/** Every stage (and nested stage) the nodes sit in, outermost first in call order. */
export function stageGroups(nodes: DagNode[], view: PipelineView): StageGroup[] {
  const groups = new Map<string, StageGroup>();
  for (const n of nodes) {
    if (n.kind !== "node") continue;
    const path = stagePath(n, view);
    const fromView = !(n.stage ?? []).length;
    for (let i = 0; i < path.length; i++) {
      const names = path.slice(0, i + 1).map((s) => s.name);
      const id = stageId(names);
      let g = groups.get(id);
      if (!g) {
        const info = path[i];
        const style = view.styles[info.name] ?? {};
        g = {
          id,
          path: names,
          name: style.label || info.name,
          description: style.description ?? info.description ?? null,
          color: stageColor(info.name, view, info.color),
          members: [],
          fromView,
        };
        groups.set(id, g);
      }
      g.members.push(n);
    }
  }
  return [...groups.values()];
}

export interface Grouping {
  fan: FanoutGroup[];
  stages: StageGroup[];
  /** node id -> the card that stands for it (a collapsed stage, else a collapsed fan-out group) */
  members: Map<string, string>;
  collapsedStages: Set<string>;
  collapsedFan: Set<string>;
  view: PipelineView;
}

/**
 * Which card each node is drawn as. A node belongs to the outermost collapsed stage on its path;
 * otherwise to its collapsed fan-out group. Stages start collapsed (the overview) unless
 * `showSteps`; `stageToggled` flips individual stages from that default.
 */
export function grouping(nodes: DagNode[], view: PipelineView, opts: { collapsedFan: Set<string>; showSteps: boolean; stageToggled: Set<string> }): Grouping {
  const fan = fanoutGroups(nodes);
  const stages = stageGroups(nodes, view);
  const collapsedStages = new Set(stages.filter((s) => !opts.showSteps !== opts.stageToggled.has(s.id)).map((s) => s.id));
  const members = new Map<string, string>();
  for (const g of fan) {
    if (opts.collapsedFan.has(g.step)) for (const m of g.members) members.set(m.id, groupId(g.step));
    else for (const m of sampleItems(g.members).hidden) members.set(m.id, moreId(g.step)); // expanded, but capped
  }
  for (const n of nodes) {
    if (n.kind !== "node") continue;
    const path = stagePath(n, view).map((s) => s.name);
    for (let i = 1; i <= path.length; i++) {
      const id = stageId(path.slice(0, i));
      if (collapsedStages.has(id)) {
        members.set(n.id, id);
        break;
      }
    }
  }
  return { fan, stages, members, collapsedStages, collapsedFan: opts.collapsedFan, view };
}

const py = (s: string) => JSON.stringify(s);

/** The view's UI-only stages as code to paste into the pipeline body. */
export function viewAsCode(view: PipelineView): string {
  const byStage = new Map<string, string[]>();
  for (const [step, s] of Object.entries(view.assign)) byStage.set(s, [...(byStage.get(s) ?? []), step]);
  return [...byStage]
    .map(([s, steps]) => {
      const style = view.styles[s] ?? {};
      const args = [py(s), style.description ? py(style.description) : null].filter(Boolean).join(", ") + (style.color ? `, color=${py(style.color)}` : "");
      return `with dendrite.stage(${args}):\n${steps.map((st) => `    ...  # call ${st}(...) here`).join("\n")}`;
    })
    .join("\n\n");
}
