import { useEffect, useMemo, useState } from "react";
import type { ArtifactRef, Dag, DagNode, PlanNode } from "../api";
import { collapseEdges, configInputs, controlEdges, fanoutGroups, memberMap, progressLine, summarize, summarizePlan } from "../fanout";
import { PLAN, REASON, STATUS, UPSTREAM_NOTE } from "../copy";
import { fmtDur, fmtSize, fmtVal } from "../format";
import { Graph, mergeEdges, type GraphNode } from "./Graph";
import { DagLegend, PlanLegend } from "./Legend";
import { nodeTypes, type HoverCard, type Port } from "./nodes";

// Placeholders (kind "pending") stand for outputs a draft hasn't computed yet; there is nothing to open.
export const toPorts = (m?: Record<string, ArtifactRef | null>): Port[] =>
  Object.entries(m ?? {}).flatMap(([name, r]) => (r && (r.kind as string) !== "pending" ? [{ name, hash: r.hash, sub: `${r.kind} · ${fmtSize(r.size)}` }] : []));

const changedParams = (n: PlanNode) => (n.param_schema ?? []).filter((p) => p.changed);

/** A planned step (draft mode): what running the draft would do, and why. */
export function planData(n: PlanNode, self: string, nameOf: (id: string) => string, onOpenSource: (exp: string, node: string) => void) {
  const outcome = n.plan ?? "run";
  const copy = PLAN[outcome];
  const origin = n.reuse_from?.experiment_id && n.reuse_from.experiment_id !== self ? n.reuse_from.experiment_id : null;
  const est = fmtDur(n.estimate);
  const changed = changedParams(n);
  const changes = changed.map((p) => `${p.name} ${fmtVal(p.inherited)} → ${fmtVal(p.value)}`);
  const why = n.reason ? REASON[n.reason] ?? n.reason : "";
  const line2 =
    outcome === "run" ? `Will run${est ? ` · about ${est}` : ""}` : outcome === "frozen" ? "Frozen to the parent's result" : `Reused${est ? ` · took ${est}` : ""}`;
  const source =
    origin && outcome !== "run"
      ? { verb: outcome === "reuse" ? "Reused from" : "Frozen to", label: nameOf(origin), rest: outcome === "reuse" ? (est ? ` · took ${est}` : "") : "'s result", open: () => onOpenSource(origin, n.name) }
      : undefined;
  const line3 = changes.length ? `✎ ${changes.join(", ")}` : outcome === "run" ? `because ${why}` : Object.keys(n.outputs ?? {}).length ? `→ ${Object.keys(n.outputs ?? {}).join(", ")}` : "";
  const facts: [string, string][] = changes.length ? changed.map((p) => [p.name, `${fmtVal(p.inherited)} → ${fmtVal(p.value)}`]) : [];
  if (est) facts.push([outcome === "run" ? "estimate" : "took", est]);
  const body = outcome === "run" ? `${copy.explain} It runs because ${why}.${n.reason === "upstream" ? ` ${UPSTREAM_NOTE}` : ""}` : copy.explain;
  return {
    status: copy.tone,
    variant: n.reason === "upstream" ? "upstream" : "",
    line2,
    line3,
    source,
    hover: { title: `${n.name} — ${outcome === "run" ? "will run" : copy.label.toLowerCase()}`, body, facts, hint: "Click to change its parameters" } as HoverCard,
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
    case "computed": return `Ran${d ? ` · took ${d}` : ""}`;
    case "cached": return `Reused${other ? ` from ${other}` : ""}${d ? ` · took ${d}` : ""}`;
    case "pinned": return `Frozen${other ? ` to ${other}'s result` : ""}`;
    case "running": return `Running${d ? ` · ${d}` : "…"}`;
    case "waiting": return "Waiting for an identical run…";
    case "queued": return "Queued";
    case "failed": return "Failed";
    default: return n.status;
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
  const body = origin && n.status === "cached" ? `${s.explain} The result comes from ${origin}'s run of this step.` : (s?.explain ?? "");
  const hint = origin ? `Click the name "${origin}" to open that run · click the step to pick its data` : "Click to select it and pick its input or output data";
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
}

export function DagView({ dag, nameOf, selected, onSelect, onArtifact, onOpenSource, activeArtifact, planned = false, collapsed = new Set(), onToggleGroup = () => {} }: Props) {
  const busy = !planned && dag.nodes.some((n) => n.status === "running");
  const now = useNow(busy);
  const groups = useMemo(() => fanoutGroups(dag.nodes), [dag.nodes]);
  const collapsedKey = [...collapsed].sort().join("|");
  const members = useMemo(() => memberMap(groups, collapsed), [groups, collapsedKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const configs = useMemo(() => configInputs(dag.nodes, dag.reads ?? []), [dag.nodes, dag.reads]);
  const nodes: GraphNode[] = useMemo(
    () =>
      dag.nodes.flatMap((n): GraphNode[] => {
        const groupKey = members.get(n.id);
        if (groupKey) {
          const g = groups.find((x) => x.id === groupKey)!;
          if (g.members[0].id !== n.id) return []; // the group is drawn once, at its first item
          const s = planned ? summarizePlan(g.members as PlanNode[]) : summarize(g.members);
          return [{
            id: g.id,
            origins: g.members.map((m) => m.id), // a collapsing group folds out of its items
            type: "pipelineNode",
            width: 250,
            height: 68,
            minimapColor: `var(--${s.status})`,
            data: {
              name: g.step,
              status: s.status,
              line2: s.line2,
              line3: s.line3,
              progress: !planned && s.done < s.total ? s.fraction : undefined,
              group: { collapsed: true, count: g.members.length, toggle: () => onToggleGroup(g.step) },
              hover: {
                title: `${g.step} — once per item`,
                body: `The pipeline calls ${g.step} once for each of ${g.members.length} items. They're shown as one card; expand it to see each item.`,
                facts: [["items", g.members.map((m) => m.key).join(", ")]],
                hint: "Click to list the items · ▸ to expand",
              },
            },
          }];
        }
        const expandedOf = n.kind === "node" ? groups.find((g) => g.members.some((m) => m.id === n.id)) : undefined;
        const member = expandedOf ? { member: { of: `${expandedOf.step} ×${expandedOf.members.length}`, collapse: () => onToggleGroup(expandedOf.step) } } : {};
        const origins = expandedOf ? [expandedOf.id] : undefined; // expanded items grow out of the group card
        if (n.kind === "input") {
          const a = n.artifact;
          const configures = configs.has(n.id);
          return [{
            id: n.id,
            type: "inputNode",
            width: 200,
            height: 56,
            minimapColor: "var(--input)",
            data: {
              name: n.name,
              sub: [a?.filename, a ? fmtSize(a.size) : null].filter(Boolean).join(" · "),
              configures,
              hover: {
                title: `Input: ${n.name}`,
                body: configures
                  ? "The pipeline itself reads this input and passes its values (settings, options) into the steps, so it has no single arrow: it configures the whole run."
                  : "A file or folder this experiment started from. Every step below depends on it.",
                facts: a ? [["file", a.filename ?? a.kind], ["size", fmtSize(a.size)]] : [],
              },
            },
          }];
        }
        if (planned) {
          const p = planData(n as PlanNode, dag.experiment.id, nameOf, onOpenSource);
          return [{
            id: n.id,
            origins,
            type: "pipelineNode",
            width: 250,
            height: 68,
            minimapColor: `var(--${p.status})`,
            data: { name: n.name, ...p, ...member, ports: { inputs: toPorts(n.inputs), outputs: toPorts(n.outputs), active: activeArtifact, onPick: onArtifact } },
          }];
        }
        const line = statusLine(n, dag.experiment.id, nameOf, now);
        const live = n.status === "running" && n.progress ? progressLine(n.progress, n.started_at ? Math.max(0, now - n.started_at) : null) : null;
        const outs = Object.keys(n.outputs ?? {});
        const origin = n.execution_experiment && n.execution_experiment !== dag.experiment.id ? n.execution_experiment : null;
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
        return [{
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
            hover: hoverFor(n, line, origin ? nameOf(origin) : null),
            ports: { inputs: toPorts(n.inputs), outputs: toPorts(n.outputs), active: activeArtifact, onPick: onArtifact },
          },
        }];
      }),
    [dag, nameOf, onArtifact, onOpenSource, activeArtifact, planned, groups, members, configs, onToggleGroup, now],
  );
  const edges = useMemo(
    () => [
      ...mergeEdges(collapseEdges(dag.edges, members)),
      ...mergeEdges(controlEdges(dag.nodes, dag.edges, dag.reads ?? [], members).map((e) => ({ ...e, dashed: true }))),
    ],
    [dag.edges, dag.nodes, dag.reads, members],
  );
  const present = useMemo(
    () =>
      new Set(
        dag.nodes
          .filter((n) => n.kind === "node")
          .map((n) => (planned ? ((n as PlanNode).reason === "upstream" ? "upstream" : (n as PlanNode).plan ?? "run") : (n.status as string))),
      ),
    [dag.nodes, planned],
  );
  if (!dag.nodes.length) {
    return <div className="empty">No steps recorded yet{dag.experiment.status === "created" ? " — this experiment hasn't been run." : "."}</div>;
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
        legend={planned ? <PlanLegend present={present} hasInputs={hasInputs} /> : <DagLegend present={present} hasInputs={hasInputs} />}
      />
      {starting && <div className="canvas-banner">Starting the run — steps appear here as they begin…</div>}
    </>
  );
}
