import { useMemo } from "react";
import type { Dag } from "../api";
import { fmtDur, short } from "../format";
import { Graph, mergeEdges, type GraphNode } from "./Graph";
import { DagLegend } from "./Legend";
import { nodeTypes } from "./nodes";

export function DagView({ dag, nameOf, selected, onSelect }: { dag: Dag; nameOf: (id: string) => string; selected?: string; onSelect: (id: string) => void }) {
  const nodes: GraphNode[] = useMemo(
    () =>
      dag.nodes.map((n) => {
        if (n.kind === "input") {
          return { id: n.id, type: "inputNode", width: 200, height: 56, minimapColor: "var(--input)", data: { name: n.name, hash: short(n.artifact?.hash) } };
        }
        const reused = n.status === "cached" && n.execution_experiment && n.execution_experiment !== dag.experiment.id ? ` · from ${nameOf(n.execution_experiment)}` : "";
        const outs = Object.keys(n.outputs ?? {});
        return {
          id: n.id,
          type: "pipelineNode",
          width: 230,
          height: 68,
          minimapColor: `var(--${n.status})`,
          data: {
            name: n.name,
            status: n.status,
            line2: `${n.status}${n.duration != null ? ` · ${fmtDur(n.duration)}` : ""}${reused}`,
            line3: outs.length ? `→ ${outs.join(", ")}` : n.signature ? `sig ${short(n.signature)}` : "",
          },
        };
      }),
    [dag, nameOf],
  );
  const edges = useMemo(() => mergeEdges(dag.edges), [dag.edges]);
  if (!dag.nodes.length) {
    return <div className="empty">No nodes recorded yet{dag.experiment.status === "created" ? " — run the experiment" : ""}.</div>;
  }
  const starting = dag.experiment.status === "running" && !dag.nodes.some((n) => n.kind === "node");
  return (
    <>
      <Graph nodes={nodes} edges={edges} nodeTypes={nodeTypes} selectedId={selected} focusId={selected} onSelect={onSelect} legend={<DagLegend />} />
      {starting && (
        <div className="legend" style={{ top: 12, bottom: "auto" }}>Starting the Prefect flow run — nodes appear as they are called…</div>
      )}
    </>
  );
}
