import { useMemo } from "react";
import type { Lineage } from "../api";
import { fmtDur, fmtSize, short } from "../format";
import { Graph, mergeEdges, type GraphNode } from "./Graph";
import { LineageLegend } from "./Legend";
import { nodeTypes } from "./nodes";

interface Props {
  lineage: Lineage;
  selected?: string;
  onSelect: (id: string) => void;
  nameOf: (id: string) => string;
  back?: { label: string; go: () => void };
}

export function LineageView({ lineage, selected, onSelect, nameOf, back }: Props) {
  const focus = lineage.artifact.hash;
  const runsByNode = useMemo(() => {
    const m = new Map<string, number>();
    for (const x of lineage.executions) m.set(x.node, (m.get(x.node) ?? 0) + 1);
    return m;
  }, [lineage.executions]);
  const nodes: GraphNode[] = useMemo(
    () => [
      ...lineage.artifacts.map((a) => {
        const isFocus = a.hash === focus;
        const where = a.producer_node ? `Produced by ${a.producer_node}.` : "A source input — nothing produced it.";
        const rel = isFocus ? "This is the result you are tracing." : a.role === "descendant" ? "Built from the traced result." : "Went into the traced result.";
        return {
          id: a.hash,
          type: "artifactNode",
          width: 200,
          height: 56,
          minimapColor: isFocus ? "var(--accent)" : "var(--data)",
          data: {
            label: a.label,
            sub: `${a.kind} · ${fmtSize(a.size)}`,
            focus: isFocus,
            hover: {
              title: `Data: ${a.label}`,
              body: `${rel} ${where}`,
              facts: [["type", a.kind + (a.filename && a.filename !== a.label ? ` (${a.filename})` : "")], ["size", fmtSize(a.size)], ["id", short(a.hash, 12)]],
              hint: "Click to preview it and see which experiments use it",
            },
          },
        };
      }),
      ...lineage.executions.map((x) => {
        const by = x.experiment_id ? nameOf(x.experiment_id) : null;
        const dup = (runsByNode.get(x.node) ?? 0) > 1;
        return {
          id: x.id,
          type: "executionNode",
          width: 200,
          height: 56,
          minimapColor: "var(--exec)",
          data: {
            node: x.node,
            sub: `${by ? `run by ${by}` : `run ${short(x.id, 8)}`}${x.finished_at ? ` · ${fmtDur(x.finished_at - x.started_at)}` : ""}`,
            hover: {
              title: `Computation: ${x.node}`,
              body: `One actual run of the ${x.node} step${by ? `, first run by ${by}` : ""}.${dup ? " Other runs of this step produced the same data, so they appear side by side." : ""}`,
              facts: [["took", fmtDur(x.finished_at != null ? x.finished_at - x.started_at : null) || "—"], ["run id", short(x.id, 12)]],
              hint: "Click for its exact inputs and parameters",
            },
          },
        };
      }),
    ],
    [lineage, focus, nameOf, runsByNode],
  );
  const edges = useMemo(() => mergeEdges(lineage.edges), [lineage.edges]);
  const banner = (
    <div className="canvas-banner lineage">
      <span>
        Tracing <b>{lineage.artifact.label}</b>: what produced it, and what was built from it, across <b>all experiments</b>.
      </span>
      {back && <button onClick={back.go}>Back to {back.label}</button>}
    </div>
  );
  if (nodes.length === 1) {
    return (
      <div className="lineage-mode" style={{ position: "absolute", inset: 0 }}>
        {banner}
        <div className="empty" style={{ paddingTop: 96 }}>Nothing produced or used this yet — it is a source input.</div>
      </div>
    );
  }
  return (
    <div className="lineage-mode" style={{ position: "absolute", inset: 0 }}>
      <Graph nodes={nodes} edges={edges} nodeTypes={nodeTypes} variant="lineage" selectedId={selected} focusId={selected ?? focus} onSelect={onSelect} legend={<LineageLegend />} />
      {banner}
    </div>
  );
}
