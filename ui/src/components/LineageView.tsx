import { useMemo } from "react";
import type { Lineage } from "../api";
import { fmtDur, fmtSize, short } from "../format";
import { Graph, mergeEdges, type GraphNode } from "./Graph";
import { LineageLegend } from "./Legend";
import { nodeTypes } from "./nodes";

export function LineageView({ lineage, selected, onSelect, nameOf }: { lineage: Lineage; selected?: string; onSelect: (id: string) => void; nameOf: (id: string) => string }) {
  const focus = lineage.artifact.hash;
  const nodes: GraphNode[] = useMemo(
    () => [
      ...lineage.artifacts.map((a) => ({
        id: a.hash,
        type: "artifactNode",
        width: 200,
        height: 56,
        minimapColor: a.hash === focus ? "var(--accent)" : "var(--data)",
        data: { label: a.label, sub: `${a.kind} · ${short(a.hash, 8)} · ${fmtSize(a.size)}`, focus: a.hash === focus },
      })),
      ...lineage.executions.map((x) => ({
        id: x.id,
        type: "executionNode",
        width: 200,
        height: 56,
        minimapColor: "var(--exec)",
        data: {
          node: x.node,
          // Identical outputs can come from several runs; say which experiment ran each one.
          sub: `${x.experiment_id ? `run by ${nameOf(x.experiment_id)}` : `exec ${short(x.id, 8)}`}${x.finished_at ? ` · ${fmtDur(x.finished_at - x.started_at)}` : ""}`,
        },
      })),
    ],
    [lineage, focus, nameOf],
  );
  const edges = useMemo(() => mergeEdges(lineage.edges), [lineage.edges]);
  if (nodes.length === 1) {
    return <div className="empty">This artifact has no recorded producers or consumers — it is a source input that nothing has read yet.</div>;
  }
  return <Graph nodes={nodes} edges={edges} nodeTypes={nodeTypes} selectedId={selected} focusId={selected ?? focus} onSelect={onSelect} legend={<LineageLegend />} />;
}
