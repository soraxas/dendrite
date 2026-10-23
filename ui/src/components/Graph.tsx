// React Flow canvas fed by ELK positions and routed edges.
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeTypes,
} from "@xyflow/react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useElkLayout, type LayoutEdge, type Point } from "../layout";
import { MOVE_MS, useTweenedPoints } from "../tween";

export interface GraphNode<T extends Record<string, unknown> = Record<string, unknown>> {
  id: string;
  type: string;
  width: number;
  height: number;
  data: T;
  minimapColor?: string;
  /** Ids this node grows out of (or folds into) when it appears, e.g. a fan-out group and its items. */
  origins?: string[];
}

export interface GraphEdge extends LayoutEdge {
  /** A control edge (what the pipeline decided from a result), not data flowing. */
  dashed?: boolean;
}

type ElkEdgeData = { points: Point[]; label?: string; labelBox?: { x: number; y: number; width: number; height: number }; hl: boolean; dashed?: boolean };

function roundedPath(pts: Point[], r = 8): string {
  if (pts.length < 2) return "";
  let d = `M${pts[0].x},${pts[0].y}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const [a, b, c] = [pts[i - 1], pts[i], pts[i + 1]];
    const d1 = Math.hypot(b.x - a.x, b.y - a.y), d2 = Math.hypot(c.x - b.x, c.y - b.y);
    const rr = Math.min(r, d1 / 2, d2 / 2);
    const p1 = { x: b.x + ((a.x - b.x) * rr) / (d1 || 1), y: b.y + ((a.y - b.y) * rr) / (d1 || 1) };
    const p2 = { x: b.x + ((c.x - b.x) * rr) / (d2 || 1), y: b.y + ((c.y - b.y) * rr) / (d2 || 1) };
    d += ` L${p1.x},${p1.y} Q${b.x},${b.y} ${p2.x},${p2.y}`;
  }
  const last = pts[pts.length - 1];
  return `${d} L${last.x},${last.y}`;
}

const NO_POINTS: Point[] = [];

function ElkEdge({ id, data, markerEnd }: EdgeProps<Edge<ElkEdgeData>>) {
  const points = useTweenedPoints(data?.points ?? NO_POINTS);
  const labelAt = useTweenedPoints(useMemo(() => (data?.labelBox ? [{ x: data.labelBox.x, y: data.labelBox.y }] : NO_POINTS), [data?.labelBox]));
  if (!data) return null;
  const stroke = data.hl ? "var(--accent)" : "var(--edge)";
  return (
    <>
      <BaseEdge
        id={id}
        path={roundedPath(points)}
        markerEnd={markerEnd}
        style={{ stroke, strokeWidth: data.hl ? 2.2 : 1.5, strokeDasharray: data.dashed ? "5 5" : undefined }}
      />
      {data.label && data.labelBox && (
        <EdgeLabelRenderer>
          <div
            className={`elk-edge-label ${data.hl ? "hl" : ""} ${data.dashed ? "control" : ""}`}
            style={{ transform: `translate(${labelAt[0]?.x ?? data.labelBox.x}px, ${labelAt[0]?.y ?? data.labelBox.y}px)` }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const edgeTypes = { elk: ElkEdge };

function useSize(ref: React.RefObject<HTMLDivElement | null>) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ width: e.contentRect.width, height: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return size;
}

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  nodeTypes: NodeTypes;
  selectedId?: string;
  /** Node to keep in view when the whole graph is too large to fit at a legible zoom. */
  focusId?: string;
  onSelect: (id: string) => void;
  legend: ReactNode;
  /** Lineage gets its own canvas look so it's obvious the graph no longer shows one experiment. */
  variant?: "pipeline" | "lineage" | "draft";
}

const MIN_LEGIBLE_ZOOM = 0.65;

function Canvas({ nodes, edges, nodeTypes, selectedId, focusId, onSelect, legend, variant = "pipeline" }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const size = useSize(box);
  const shapeKey = useMemo(
    () => nodes.map((n) => n.id).join("|") + "#" + edges.map((e) => `${e.from}>${e.to}`).join("|"),
    [nodes, edges],
  );
  const layout = useElkLayout(nodes, edges, shapeKey, size);
  const fitted = useRef(false);
  const { fitView, getZoom, setCenter, getViewport } = useReactFlow();
  // Appearing nodes first render where they come from (their `origins` in the previous layout),
  // then move to their place once that has painted; the CSS transition animates the move.
  const previous = useRef<Map<string, Point>>(new Map());
  const [settled, setSettled] = useState<string | null>(null);
  useEffect(() => {
    if (!layout) return;
    let inner = 0;
    const outer = requestAnimationFrame(() => (inner = requestAnimationFrame(() => setSettled(layout.key))));
    return () => (cancelAnimationFrame(outer), cancelAnimationFrame(inner));
  }, [layout]);
  useEffect(() => {
    if (layout && settled === layout.key) previous.current = layout.nodes;
  }, [layout, settled]);
  const startAt = (n: GraphNode): Point | undefined => {
    if (!layout || settled === layout.key || previous.current.has(n.id)) return undefined;
    const from = (n.origins ?? []).map((o) => previous.current.get(o)).filter((p): p is Point => !!p);
    if (!from.length) return undefined;
    return { x: from.reduce((s, p) => s + p.x, 0) / from.length, y: from.reduce((s, p) => s + p.y, 0) / from.length };
  };

  const incident = useMemo(() => {
    const s = new Set<string>();
    if (selectedId) for (const e of edges) if (e.from === selectedId || e.to === selectedId) s.add(e.id);
    return s;
  }, [edges, selectedId]);
  const rfNodes: Node[] = useMemo(() => {
    if (!layout) return [];
    return nodes
      .filter((n) => layout.nodes.has(n.id))
      .map((n) => ({
        id: n.id,
        type: n.type,
        position: startAt(n) ?? layout.nodes.get(n.id)!,
        width: n.width,
        height: n.height,
        // Without `measured`, React Flow treats every updated node as new and drops its handle
        // positions until it re-measures, so every edge unmounts for a frame on each update.
        measured: { width: n.width, height: n.height },
        data: { ...n.data, dir: layout.dir, selected: n.id === selectedId },
        className: startAt(n) ? "from-origin" : undefined,
        draggable: false,
        selectable: false,
      }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout, nodes, selectedId, settled]);

  const rfEdges: Edge<ElkEdgeData>[] = useMemo(() => {
    if (!layout) return [];
    return edges
      .filter((e) => layout.edges.has(e.id))
      .map((e) => {
        const r = layout.edges.get(e.id)!;
        const hl = incident.has(e.id);
        return {
          id: e.id, // stable, so a moved edge morphs to its new route (ElkEdge) instead of being replaced
          source: e.from,
          target: e.to,
          type: "elk",
          markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: hl ? "var(--accent)" : "var(--edge)" },
          data: { points: r.points, label: e.label, labelBox: r.label, hl, dashed: e.dashed },
        };
      });
  }, [layout, edges, incident]);

  // Re-fit only when the graph's shape changes, never on status polls. If fitting everything
  // would make text illegible, centre the focus node at a readable zoom instead.
  useEffect(() => {
    if (!layout) return;
    const first = !fitted.current;
    fitted.current = true;
    if (!first) {
      // Keep the view still while nodes move. Refit only if the new layout no longer fits, and
      // only once they have settled, so the two motions never fight.
      const { x, y, zoom } = getViewport();
      const view = { left: -x / zoom, top: -y / zoom, right: (size.width - x) / zoom, bottom: (size.height - y) / zoom };
      let fits = true;
      for (const n of nodes) {
        const p = layout.nodes.get(n.id);
        if (p && (p.x < view.left || p.y < view.top || p.x + n.width > view.right || p.y + n.height > view.bottom)) fits = false;
      }
      if (fits) return;
      const t = setTimeout(() => fitView({ padding: 0.12, maxZoom: 1.2, minZoom: MIN_LEGIBLE_ZOOM, duration: 300 }), MOVE_MS);
      return () => clearTimeout(t);
    }
    requestAnimationFrame(async () => {
      await fitView({ padding: 0.12, maxZoom: 1.2, minZoom: 0.2 });
      const focus = focusId ? layout.nodes.get(focusId) : undefined;
      const spec = focusId ? nodes.find((n) => n.id === focusId) : undefined;
      if (getZoom() < MIN_LEGIBLE_ZOOM && focus && spec) {
        await setCenter(focus.x + spec.width / 2, focus.y + spec.height / 2 - 120, { zoom: 0.85 });
      } else if (getZoom() < MIN_LEGIBLE_ZOOM) {
        await fitView({ padding: 0.12, minZoom: MIN_LEGIBLE_ZOOM, maxZoom: MIN_LEGIBLE_ZOOM });
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout]);

  // Re-fit when the canvas is resized (e.g. the sidebar collapses), debounced.
  const fittedSize = useRef<{ width: number; height: number } | null>(null);
  useEffect(() => {
    if (!layout || !size.width) return;
    const prev = fittedSize.current;
    fittedSize.current = size;
    if (!prev || (Math.abs(prev.width - size.width) < 2 && Math.abs(prev.height - size.height) < 2)) return;
    const t = setTimeout(() => fitView({ padding: 0.12, maxZoom: 1.2, minZoom: MIN_LEGIBLE_ZOOM, duration: 150 }), 150);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size.width, size.height, layout]);

  return (
    <div ref={box} style={{ position: "absolute", inset: 0 }}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        colorMode="system"
        minZoom={0.2}
        maxZoom={2.5}
        nodesConnectable={false}
        nodesDraggable={false}
        onNodeClick={(_, n) => onSelect(n.id)}
        proOptions={{ hideAttribution: true }}
      >
        {variant === "lineage" ? (
          <Background variant={BackgroundVariant.Cross} gap={28} size={6} color="var(--lineage-grid)" bgColor="var(--lineage-bg)" />
        ) : variant === "draft" ? (
          <Background variant={BackgroundVariant.Lines} gap={28} size={1} color="var(--draft-grid)" bgColor="var(--draft-bg)" />
        ) : (
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="var(--line-strong)" />
        )}
        <Controls showInteractive={false} position="top-right" />
        {nodes.length > 8 && (
          <MiniMap
            pannable
            zoomable
            position="bottom-right"
            nodeColor={(n) => nodes.find((x) => x.id === n.id)?.minimapColor ?? "var(--edge)"}
            maskColor="color-mix(in srgb, var(--bg) 70%, transparent)"
            style={{ background: "var(--panel)" }}
          />
        )}
      </ReactFlow>
      {legend}
      {!layout && <div className="empty" style={{ position: "absolute", inset: 0 }}>Laying out…</div>}
    </div>
  );
}

export function Graph(props: Props) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}

/** Merge parallel edges between the same pair of nodes into one edge with a combined label. */
export function mergeEdges(edges: { from: string; to: string; label: string; dashed?: boolean }[]): GraphEdge[] {
  const merged = new Map<string, GraphEdge>();
  for (const e of edges) {
    const id = `${e.from}${e.dashed ? "~>" : "->"}${e.to}`;
    const m = merged.get(id);
    if (!m) merged.set(id, { id, from: e.from, to: e.to, label: e.label, ...(e.dashed ? { dashed: true } : {}) });
    else if (e.label && !(m.label ?? "").split(", ").includes(e.label)) m.label = m.label ? `${m.label}, ${e.label}` : e.label;
  }
  return [...merged.values()];
}
