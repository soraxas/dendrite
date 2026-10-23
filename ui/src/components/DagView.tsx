import { useMemo } from "react";
import type { ArtifactRef, Dag, DagNode, PlanNode } from "../api";
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

/** The node's one-line status, in user terms ("Reused from baseline · took 314ms"). */
function statusLine(n: DagNode, self: string, nameOf: (id: string) => string): string {
  const other = n.execution_experiment && n.execution_experiment !== self ? nameOf(n.execution_experiment) : null;
  const d = fmtDur(n.duration);
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
}

export function DagView({ dag, nameOf, selected, onSelect, onArtifact, onOpenSource, activeArtifact, planned = false }: Props) {
  const nodes: GraphNode[] = useMemo(
    () =>
      dag.nodes.map((n) => {
        if (n.kind === "input") {
          const a = n.artifact;
          return {
            id: n.id,
            type: "inputNode",
            width: 200,
            height: 56,
            minimapColor: "var(--input)",
            data: {
              name: n.name,
              sub: [a?.filename, a ? fmtSize(a.size) : null].filter(Boolean).join(" · "),
              hover: { title: `Input: ${n.name}`, body: "A file or folder this experiment started from. Every step below depends on it.", facts: a ? [["file", a.filename ?? a.kind], ["size", fmtSize(a.size)]] : [] },
            },
          };
        }
        if (planned) {
          const p = planData(n as PlanNode, dag.experiment.id, nameOf, onOpenSource);
          return {
            id: n.id,
            type: "pipelineNode",
            width: 250,
            height: 68,
            minimapColor: `var(--${p.status})`,
            data: { name: n.name, ...p, ports: { inputs: toPorts(n.inputs), outputs: toPorts(n.outputs), active: activeArtifact, onPick: onArtifact } },
          };
        }
        const line = statusLine(n, dag.experiment.id, nameOf);
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
        return {
          id: n.id,
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
            hover: hoverFor(n, line, origin ? nameOf(origin) : null),
            ports: { inputs: toPorts(n.inputs), outputs: toPorts(n.outputs), active: activeArtifact, onPick: onArtifact },
          },
        };
      }),
    [dag, nameOf, onArtifact, onOpenSource, activeArtifact, planned],
  );
  const edges = useMemo(() => mergeEdges(dag.edges), [dag.edges]);
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
