import { useEffect, useMemo, useState } from "react";
import type { ArtifactRef, Dag, DagNode, PlanNode } from "../api";
import { PLAN, REASON, STATUS, UPSTREAM_NOTE } from "../copy";
import {
  collapseEdges,
  configInputs,
  controlEdges,
  fanFrameId,
  gridCells,
  gridRows,
  moreId,
  progressLine,
  sampleItems,
  summarize,
  summarizePlan,
} from "../fanout";
import { fmtDur, fmtSize, fmtVal } from "../format";
import { grouping as computeGrouping, EMPTY_VIEW, type Grouping, stageId, stagePath } from "../stages";
import { Graph, type GraphNode, mergeEdges } from "./Graph";
import { DagLegend, PlanLegend } from "./Legend";
import { type HoverCard, nodeTypes, type Port } from "./nodes";

// Placeholders (kind "pending") stand for outputs a draft hasn't computed yet; there is nothing to open.
export const toPorts = (m?: Record<string, ArtifactRef | null>): Port[] =>
  Object.entries(m ?? {}).flatMap(([name, r]) =>
    r && (r.kind as string) !== "pending"
      ? [{ name, hash: r.hash, kind: r.kind, filename: r.filename, sub: `${r.kind} · ${fmtSize(r.size)}` }]
      : [],
  );

const changedParams = (n: PlanNode) => (n.param_schema ?? []).filter((p) => p.changed);

/** A planned step (draft mode): what running the draft would do, and why. */
export function planData(
  n: PlanNode,
  self: string,
  nameOf: (id: string) => string,
  onOpenSource: (exp: string, node: string) => void,
) {
  const outcome = n.plan ?? "run";
  const copy = PLAN[outcome];
  const origin = n.reuse_from?.experiment_id && n.reuse_from.experiment_id !== self ? n.reuse_from.experiment_id : null;
  const est = fmtDur(n.estimate);
  const changed = changedParams(n);
  const changes = changed.map((p) => `${p.name} ${fmtVal(p.inherited)} → ${fmtVal(p.value)}`);
  const why = n.reason ? (REASON[n.reason] ?? n.reason) : "";
  const line2 =
    outcome === "run"
      ? `Will run${est ? ` · about ${est}` : ""}`
      : outcome === "frozen"
        ? "Frozen to the parent's result"
        : `Reused${est ? ` · took ${est}` : ""}`;
  const source =
    origin && outcome !== "run"
      ? {
          verb: outcome === "reuse" ? "Reused from" : "Frozen to",
          label: nameOf(origin),
          rest: outcome === "reuse" ? (est ? ` · took ${est}` : "") : "'s result",
          open: () => onOpenSource(origin, n.name),
        }
      : undefined;
  const line3 = changes.length
    ? `✎ ${changes.join(", ")}`
    : outcome === "run"
      ? `because ${why}`
      : Object.keys(n.outputs ?? {}).length
        ? `→ ${Object.keys(n.outputs ?? {}).join(", ")}`
        : "";
  const facts: [string, string][] = changes.length
    ? changed.map((p) => [p.name, `${fmtVal(p.inherited)} → ${fmtVal(p.value)}`])
    : [];
  if (est) facts.push([outcome === "run" ? "estimate" : "took", est]);
  const body =
    outcome === "run"
      ? `${copy.explain} It runs because ${why}.${n.reason === "upstream" ? ` ${UPSTREAM_NOTE}` : ""}`
      : copy.explain;
  return {
    status: copy.tone,
    variant: n.reason === "upstream" ? "upstream" : "",
    line2,
    line3,
    source,
    hover: {
      title: `${n.name} — ${outcome === "run" ? "will run" : copy.label.toLowerCase()}`,
      body,
      facts,
      hint: "Click to change its parameters",
    } as HoverCard,
  };
}

/** Seconds since ``t``, re-rendering every second while ``active`` (running steps tick without polling). */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now() / 1000); // not the time the page mounted
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}

/** The node's one-line status, in user terms ("Reused from baseline · took 314ms"). */
function statusLine(n: DagNode, self: string, nameOf: (id: string) => string, now?: number): string {
  const other = n.execution_experiment && n.execution_experiment !== self ? nameOf(n.execution_experiment) : null;
  const elapsed = n.status === "running" && n.started_at && now ? Math.max(0, now - n.started_at) : null;
  const d = fmtDur(elapsed ?? n.duration);
  if (n.status === "running" && n.progress) return `Running · ${progressLine(n.progress, elapsed).text}`;
  switch (n.status) {
    case "computed":
      return `Ran${d ? ` · took ${d}` : ""}`;
    case "cached":
      return `Reused${other ? ` from ${other}` : ""}${d ? ` · took ${d}` : ""}`;
    case "pinned":
      return `Frozen${other ? ` to ${other}'s result` : ""}`;
    case "running":
      return `Running${d ? ` · ${d}` : "…"}`;
    case "waiting":
      return "Waiting for an identical run…";
    case "queued":
      return "Queued";
    case "failed":
      return "Failed";
    default:
      return n.status;
  }
}

function hoverFor(n: DagNode, line: string, origin: string | null): HoverCard {
  const s = STATUS[n.status as keyof typeof STATUS];
  const facts: [string, string][] = [];
  const params = Object.entries(n.params ?? {});
  for (const [k, v] of params.slice(0, 4)) facts.push([k, fmtVal(v)]);
  if (params.length > 4) facts.push(["…", `${params.length - 4} more parameters`]);
  const outs = Object.keys(n.outputs ?? {});
  if (outs.length) facts.push(["produces", outs.join(", ")]);
  const body =
    origin && n.status === "cached"
      ? `${s.explain} The result comes from ${origin}'s run of this step.`
      : (s?.explain ?? "");
  const hint = origin
    ? `Click the name "${origin}" to open that run · click the step to pick its data`
    : "Click to select it and pick its input or output data";
  return { title: `${n.name} — ${line}`, body, facts, hint };
}

interface Props {
  dag: Dag;
  nameOf: (id: string) => string;
  selected?: string;
  onSelect: (id: string) => void;
  onArtifact: (hash: string) => void;
  /** Open another experiment with a step selected (follow a reused result to its origin). */
  onOpenSource: (expId: string, node: string) => void;
  /** Data currently selected in the panel; its chip stays highlighted on the selected step. */
  activeArtifact?: string;
  /** `dag` is a draft's plan: show what running it would do instead of run statuses. */
  planned?: boolean;
  /** Fan-out steps shown as one card (see `fanout.ts`), and how to flip one. */
  collapsed?: Set<string>;
  onToggleGroup?: (step: string) => void;
  /** Stage and fan-out cards (see `stages.ts`); computed from `collapsed` alone when absent. */
  grouping?: Grouping;
  onToggleStage?: (id: string) => void;
  /** The Overview / All steps switch, shown when the pipeline has stages. */
  showSteps?: boolean;
  onShowSteps?: (show: boolean) => void;
}

/**
 * Sub flows: expanded stages and fan-outs become frames around their steps, a big expanded
 * fan-out gets a "+N more" node, and every card is placed in its innermost frame. Frames that
 * would be empty are dropped.
 */
export function framed(
  cards: GraphNode[],
  nodes: DagNode[],
  g: Grouping,
  planned: boolean,
  onToggleStage: (id: string) => void,
  onToggleGroup: (step: string) => void,
): GraphNode[] {
  const open = new Set(g.stages.filter((s) => !g.collapsedStages.has(s.id)).map((s) => s.id));
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const innermost = (path: string[]) => {
    let best: string | undefined;
    for (let i = 1; i <= path.length; i++) {
      const id = stageId(path.slice(0, i));
      if (!open.has(id)) break;
      best = id;
    }
    return best;
  };
  const pathOf = (n: DagNode | undefined) => (n ? stagePath(n, g.view).map((x) => x.name) : []);
  const openFan = g.fan.filter(
    (f) => !g.collapsedFan.has(f.step) && !f.members.every((m) => g.members.get(m.id)?.startsWith("stage:")),
  );
  const fanOf = new Map<string, string>();
  for (const f of openFan) for (const m of f.members) fanOf.set(m.id, fanFrameId(f.step));
  const out: GraphNode[] = [];
  for (const c of cards) {
    let parent: string | undefined;
    if (c.id.startsWith("stage:")) parent = innermost(c.id.slice(6).split("/").slice(0, -1));
    else if (c.id.startsWith("group:"))
      parent = innermost(pathOf(g.fan.find((f) => `group:${f.step}` === c.id)?.members[0]));
    else parent = fanOf.get(c.id) ?? innermost(pathOf(byId.get(c.id)));
    out.push(parent ? { ...c, parent } : c);
  }
  for (const f of openFan) {
    const { shown, hidden } = sampleItems(f.members);
    const frame = fanFrameId(f.step);
    if (hidden.length) {
      out.push({
        id: moreId(f.step),
        parent: frame,
        type: "moreNode",
        width: 220,
        height: 52,
        data: {
          label: `+${hidden.length} more ${f.step}`,
          sub: "click to list every item",
          hover: {
            title: `${hidden.length} more items`,
            body: `Only ${shown.length} of ${f.members.length} items are drawn: failed and running ones first. The full list, with filters, is in the panel.`,
            facts: [],
            hint: "Click to list them all",
          },
        },
      });
    }
    const s = planned ? summarizePlan(f.members as PlanNode[]) : summarize(f.members);
    out.push({
      id: frame,
      frame: true,
      pack: true,
      parent: innermost(pathOf(f.members[0])),
      type: "frameNode",
      width: 0,
      height: 0,
      data: {
        name: `${f.step} ×${f.members.length}`,
        sub: hidden.length ? `showing ${shown.length} of ${f.members.length} · ${s.line2}` : s.line2,
        color: "gray",
        foldLabel: "▴ Fold into one card",
        collapse: () => onToggleGroup(f.step),
      },
    });
  }
  for (const st of g.stages) {
    if (!open.has(st.id) || st.path.slice(0, -1).some((_, i) => !open.has(stageId(st.path.slice(0, i + 1))))) continue;
    const count = st.members.length;
    out.push({
      id: st.id,
      frame: true,
      parent: innermost(st.path.slice(0, -1)),
      type: "frameNode",
      width: 0,
      height: 0,
      data: {
        name: st.name,
        sub: [st.description, `${count} step${count === 1 ? "" : "s"}`].filter(Boolean).join(" · "),
        color: st.color,
        foldLabel: "▴ Fold",
        collapse: () => onToggleStage(st.id),
      },
    });
  }
  // Drop frames with nothing inside (e.g. every step of a stage folded into a fan-out elsewhere).
  let kept = out;
  for (;;) {
    const used = new Set(kept.map((n) => n.parent).filter(Boolean));
    const next = kept.filter((n) => !n.frame || used.has(n.id));
    const orphaned = new Set(kept.filter((n) => !next.includes(n)).map((n) => n.id));
    if (!orphaned.size) return next.map((n) => (n.parent && orphaned.has(n.parent) ? { ...n, parent: undefined } : n));
    kept = next.map((n) => (n.parent && orphaned.has(n.parent) ? { ...n, parent: undefined } : n));
  }
}

export function DagView({
  dag,
  nameOf,
  selected,
  onSelect,
  onArtifact,
  onOpenSource,
  activeArtifact,
  planned = false,
  collapsed = new Set(),
  onToggleGroup = () => {},
  grouping,
  onToggleStage = () => {},
  showSteps = false,
  onShowSteps,
}: Props) {
  const busy = !planned && dag.nodes.some((n) => n.status === "running");
  const now = useNow(busy);
  const collapsedKey = [...collapsed].sort().join("|");
  // biome-ignore lint/correctness/useExhaustiveDependencies: keyed on collapsedKey so an equal Set doesn't recompute
  const own = useMemo(
    () => computeGrouping(dag.nodes, EMPTY_VIEW, { collapsedFan: collapsed, showSteps: true, stageToggled: new Set() }),
    [dag.nodes, collapsedKey],
  );
  const g0 = grouping ?? own;
  const groups = g0.fan;
  const members = g0.members;
  const stages = g0.stages;
  const configs = useMemo(() => configInputs(dag.nodes, dag.reads ?? []), [dag.nodes, dag.reads]);
  const cards: GraphNode[] = useMemo(
    () =>
      dag.nodes.flatMap((n): GraphNode[] => {
        const groupKey = members.get(n.id);
        if (groupKey?.startsWith("stage:")) {
          const st = stages.find((x) => x.id === groupKey)!;
          if (st.members[0].id !== n.id) return []; // the stage is drawn once, at its first step
          const s = planned ? summarizePlan(st.members as PlanNode[]) : summarize(st.members);
          const count = st.members.length;
          return [
            {
              id: st.id,
              origins: st.members.map((m) => m.id),
              type: "pipelineNode",
              width: 300,
              height: 84,
              minimapColor: `var(--stage-${st.color})`,
              data: {
                name: st.name,
                status: s.status,
                line2: st.description ?? `${count} step${count === 1 ? "" : "s"}`,
                line3: `${st.description ? `${count} step${count === 1 ? "" : "s"} · ` : ""}${s.line2}`,
                progress: !planned && s.done < s.total ? s.fraction : undefined,
                stageCard: { color: st.color, collapsed: true, toggle: () => onToggleStage(st.id) },
                hover: {
                  title: `${st.name} — a stage of ${count} step${count === 1 ? "" : "s"}`,
                  body: `${st.fromView ? "Grouped in the UI (not in code)." : "A dendrite.stage in the pipeline code."} Expand it to see its steps; the arrows show data flowing between stages.`,
                  facts: [["steps", [...new Set(st.members.map((m) => m.step ?? m.name))].join(", ")]],
                  hint: "Click for details · ▸ to expand",
                },
              },
            },
          ];
        }
        if (groupKey?.startsWith("more:")) return []; // not drawn: counted in its fan-out's "+N more" node
        if (groupKey) {
          const g = groups.find((x) => x.id === groupKey)!;
          if (g.members[0].id !== n.id) return []; // the group is drawn once, at its first item
          const s = planned ? summarizePlan(g.members as PlanNode[]) : summarize(g.members);
          const grid = planned ? undefined : gridCells(g.members);
          return [
            {
              id: g.id,
              origins: g.members.map((m) => m.id), // a collapsing group folds out of its items
              type: "pipelineNode",
              width: 250,
              height: 68 + (grid ? gridRows(grid.length) * 9 + 6 : 0),
              minimapColor: `var(--${s.status})`,
              data: {
                name: g.step,
                status: s.status,
                line2: s.line2,
                line3: s.line3,
                progress: !planned && s.done < s.total ? s.fraction : undefined,
                group: { collapsed: true, count: g.members.length, toggle: () => onToggleGroup(g.step) },
                grid,
                onPickItem: (id: string) => onSelect(id),
                hover: {
                  title: `${g.step} — once per item`,
                  body: `The pipeline calls ${g.step} once for each of ${g.members.length} items. They're shown as one card; expand it to see each item.`,
                  facts: [
                    [
                      "items",
                      g.members.length > 12
                        ? `${g.members
                            .slice(0, 6)
                            .map((m) => m.key)
                            .join(", ")} … ${g.members[g.members.length - 1].key}`
                        : g.members.map((m) => m.key).join(", "),
                    ],
                  ],
                  hint: "Click to list the items · ▸ to expand",
                },
              },
            },
          ];
        }
        const expandedOf = n.kind === "node" ? groups.find((g) => g.members.some((m) => m.id === n.id)) : undefined;
        const path = n.kind === "node" ? stagePath(n, g0.view).map((x) => x.name) : [];
        const stageTag = {}; // membership is shown by the frame around the step
        const member = {};
        // Grow out of the card the step was folded into.
        const origins = expandedOf
          ? [expandedOf.id]
          : path.length
            ? path.map((_, i) => stageId(path.slice(0, i + 1)))
            : undefined;
        if (n.kind === "input") {
          const a = n.artifact;
          const configures = configs.has(n.id);
          return [
            {
              id: n.id,
              type: "inputNode",
              width: 200,
              height: 56,
              minimapColor: "var(--input)",
              data: {
                name: n.name,
                sub: [a?.filename, a ? fmtSize(a.size) : null].filter(Boolean).join(" · "),
                kind: a?.kind,
                filename: a?.filename,
                configures,
                hover: {
                  title: `Input: ${n.name}`,
                  body: configures
                    ? "The pipeline itself reads this input and passes its values (settings, options) into the steps, so it has no single arrow: it configures the whole run."
                    : "A file or folder this experiment started from. Every step below depends on it.",
                  facts: a
                    ? [
                        ["file", a.filename ?? a.kind],
                        ["size", fmtSize(a.size)],
                      ]
                    : [],
                },
              },
            },
          ];
        }
        if (planned) {
          const p = planData(n as PlanNode, dag.experiment.id, nameOf, onOpenSource);
          return [
            {
              id: n.id,
              origins,
              type: "pipelineNode",
              width: 250,
              height: 68,
              minimapColor: `var(--${p.status})`,
              data: {
                name: n.name,
                ...p,
                ...member,
                ...stageTag,
                ports: {
                  inputs: toPorts(n.inputs),
                  outputs: toPorts(n.outputs),
                  active: activeArtifact,
                  onPick: onArtifact,
                },
              },
            },
          ];
        }
        const line = statusLine(n, dag.experiment.id, nameOf, now);
        const live =
          n.status === "running" && n.progress
            ? progressLine(n.progress, n.started_at ? Math.max(0, now - n.started_at) : null)
            : null;
        const outs = Object.keys(n.outputs ?? {});
        const origin =
          n.execution_experiment && n.execution_experiment !== dag.experiment.id ? n.execution_experiment : null;
        const d = fmtDur(n.duration);
        const source =
          origin && (n.status === "cached" || n.status === "pinned")
            ? {
                verb: n.status === "cached" ? "Reused from" : "Frozen to",
                label: nameOf(origin),
                rest: n.status === "cached" ? (d ? ` · took ${d}` : "") : "'s result",
                open: () => onOpenSource(origin, n.name),
              }
            : undefined;
        return [
          {
            id: n.id,
            origins,
            type: "pipelineNode",
            width: 250,
            height: 68,
            minimapColor: `var(--${n.status})`,
            data: {
              name: n.name,
              status: n.status,
              line2: line,
              line3: outs.length ? `→ ${outs.join(", ")}` : "",
              source,
              progress: live?.fraction,
              ...member,
              ...stageTag,
              hover: hoverFor(n, line, origin ? nameOf(origin) : null),
              ports: {
                inputs: toPorts(n.inputs),
                outputs: toPorts(n.outputs),
                active: activeArtifact,
                onPick: onArtifact,
              },
            },
          },
        ];
      }),
    [
      dag,
      nameOf,
      onArtifact,
      onOpenSource,
      activeArtifact,
      planned,
      groups,
      members,
      stages,
      configs,
      onToggleGroup,
      onToggleStage,
      onSelect,
      now,
      g0,
    ],
  );
  const nodes = useMemo(
    () => framed(cards, dag.nodes, g0, planned, onToggleStage, onToggleGroup),
    [cards, dag.nodes, g0, planned, onToggleStage, onToggleGroup],
  );
  // Items of an expanded fan-out are parallel siblings: their edges attach to the frame around
  // them, so 12 drawn items (or 1000 counted ones) give one edge in and one out.
  const routed = useMemo(() => {
    const m = new Map(members);
    for (const n of nodes) if (n.parent?.startsWith("fan:") && !n.frame) m.set(n.id, n.parent);
    for (const [id, to] of members) if (to.startsWith("more:")) m.set(id, `fan:${to.slice(5)}`);
    return m;
  }, [members, nodes]);
  const edges = useMemo(
    () => [
      ...mergeEdges(collapseEdges(dag.edges, routed)),
      ...mergeEdges(controlEdges(dag.nodes, dag.edges, dag.reads ?? [], routed).map((e) => ({ ...e, dashed: true }))),
    ],
    [dag.edges, dag.nodes, dag.reads, routed],
  );
  const present = useMemo(
    () =>
      new Set(
        dag.nodes
          .filter((n) => n.kind === "node")
          .map((n) =>
            planned
              ? (n as PlanNode).reason === "upstream"
                ? "upstream"
                : ((n as PlanNode).plan ?? "run")
              : (n.status as string),
          ),
      ),
    [dag.nodes, planned],
  );
  if (!dag.nodes.length) {
    return (
      <div className="empty">
        No steps recorded yet{dag.experiment.status === "created" ? " — this experiment hasn't been run." : "."}
      </div>
    );
  }
  const hasInputs = dag.nodes.some((n) => n.kind === "input");
  const starting = dag.experiment.status === "running" && !dag.nodes.some((n) => n.kind === "node");
  return (
    <>
      <Graph
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        selectedId={selected}
        focusId={selected}
        onSelect={onSelect}
        variant={planned ? "draft" : "pipeline"}
        legend={
          planned ? (
            <PlanLegend present={present} hasInputs={hasInputs} />
          ) : (
            <DagLegend present={present} hasInputs={hasInputs} />
          )
        }
      />
      {starting && <div className="canvas-banner">Starting the run — steps appear here as they begin…</div>}
      {stages.length > 0 && onShowSteps && (
        <div className="stage-switch" role="group" aria-label="Detail">
          <button className={!showSteps ? "active" : ""} onClick={() => onShowSteps(false)} title="One card per stage">
            Overview
          </button>
          <button className={showSteps ? "active" : ""} onClick={() => onShowSteps(true)} title="Every step">
            All steps
          </button>
        </div>
      )}
    </>
  );
}
