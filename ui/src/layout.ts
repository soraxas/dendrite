// ELK layered layout in a web worker, cached per graph shape so status polls never re-layout.
import ELK, { type ElkNode } from "elkjs/lib/elk-api";
import workerUrl from "elkjs/lib/elk-worker.min.js?url";
import { useEffect, useRef, useState } from "react";

// Created on first layout, so importing graph modules (e.g. in tests) never spawns a worker.
let elk: InstanceType<typeof ELK> | undefined;

export interface LayoutNode {
  id: string;
  width: number;
  height: number;
  /** Id of the frame (sub flow) this node sits in; frames are laid out around their children. */
  parent?: string;
  /** A frame: sized by ELK to fit its children, with room for its header. */
  frame?: boolean;
  /** A frame whose children are parallel items with no edges between them: packed in a grid. */
  pack?: boolean;
}

export const FRAME_HEADER = 46;
const FRAME_PADDING = `[top=${FRAME_HEADER + 14},left=18,bottom=18,right=18]`;
export interface LayoutEdge {
  id: string;
  from: string;
  to: string;
  label?: string;
}
export interface Point {
  x: number;
  y: number;
}
export interface Layout {
  key: string;
  dir: "RIGHT" | "DOWN";
  width: number;
  height: number;
  /** Absolute positions (flow coordinates) of every node, frames included. */
  nodes: Map<string, Point>;
  /** Positions relative to the node's frame (equal to `nodes` for top-level nodes). */
  relative: Map<string, Point>;
  /** Sizes ELK gave frames. */
  sizes: Map<string, { width: number; height: number }>;
  edges: Map<string, { points: Point[]; label?: { x: number; y: number; width: number; height: number } }>;
}

const LABEL_CHAR_W = 6.6;

async function run(nodes: LayoutNode[], edges: LayoutEdge[], dir: "RIGHT" | "DOWN", key: string): Promise<Layout> {
  const ids = new Set(nodes.map((n) => n.id));
  const hasFrames = nodes.some((n) => n.frame);
  // Frames contain their children (ELK's compound layout); everything else is a leaf.
  const elkNode = (n: LayoutNode): ElkNode =>
    n.frame
      ? {
          id: n.id,
          layoutOptions: n.pack
            ? {
                "elk.padding": FRAME_PADDING,
                "elk.hierarchyHandling": "SEPARATE_CHILDREN",
                "elk.algorithm": "rectpacking",
                "elk.aspectRatio": "1.8",
                "elk.spacing.nodeNode": "18",
              }
            : { "elk.padding": FRAME_PADDING, "elk.direction": dir },
          children: nodes.filter((c) => c.parent === n.id).map(elkNode),
        }
      : { id: n.id, width: n.width, height: n.height };
  const graph: ElkNode = {
    id: "root",
    layoutOptions: {
      ...(hasFrames ? { "elk.hierarchyHandling": "INCLUDE_CHILDREN" } : {}),
      "elk.algorithm": "layered",
      "elk.direction": dir,
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.layered.spacing.nodeNodeBetweenLayers": "64",
      "elk.spacing.nodeNode": "32",
      "elk.layered.spacing.edgeNodeBetweenLayers": "22",
      "elk.spacing.edgeEdge": "12",
      "elk.spacing.edgeLabel": "4",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.edgeLabels.placement": "CENTER",
    },
    children: nodes.filter((n) => !n.parent || !ids.has(n.parent)).map(elkNode),
    edges: edges
      .filter((e) => ids.has(e.from) && ids.has(e.to) && e.from !== e.to)
      .map((e) => ({
        id: e.id,
        sources: [e.from],
        targets: [e.to],
        labels: e.label ? [{ text: e.label, width: e.label.length * LABEL_CHAR_W + 10, height: 15 }] : [],
      })),
  };
  // biome-ignore lint/suspicious/noAssignInExpressions: the worker is created lazily, on first layout
  const out = await (elk ??= new ELK({ workerUrl })).layout(graph);
  const pos = new Map<string, Point>();
  const rel = new Map<string, Point>();
  const sizes = new Map<string, { width: number; height: number }>();
  const walk = (cs: ElkNode[] | undefined, ox: number, oy: number) => {
    for (const c of cs ?? []) {
      const r = { x: c.x ?? 0, y: c.y ?? 0 };
      rel.set(c.id, r);
      pos.set(c.id, { x: ox + r.x, y: oy + r.y });
      if (c.children?.length) {
        sizes.set(c.id, { width: c.width ?? 0, height: c.height ?? 0 });
        walk(c.children, ox + r.x, oy + r.y);
      }
    }
  };
  walk(out.children, 0, 0);
  // Edge routes are relative to the edge's container (the frame holding both ends, or the root).
  const routed: Layout["edges"] = new Map();
  for (const e of out.edges ?? []) {
    const s = e.sections?.[0];
    if (!s) continue;
    const container = (e as { container?: string }).container;
    const o = container && container !== "root" ? (pos.get(container) ?? { x: 0, y: 0 }) : { x: 0, y: 0 };
    const at = (p: Point) => ({ x: p.x + o.x, y: p.y + o.y });
    const l = e.labels?.[0];
    routed.set(e.id, {
      points: [s.startPoint, ...(s.bendPoints ?? []), s.endPoint].map(at),
      label: l ? { x: (l.x ?? 0) + o.x, y: (l.y ?? 0) + o.y, width: l.width ?? 0, height: l.height ?? 0 } : undefined,
    });
  }
  return { key, dir, width: out.width ?? 0, height: out.height ?? 0, nodes: pos, relative: rel, sizes, edges: routed };
}

function fitScale(l: Layout, w: number, h: number) {
  return Math.min((w - 80) / Math.max(l.width, 1), (h - 80) / Math.max(l.height, 1));
}

// Direction is chosen against this fixed frame, not the window, so resizing never flips it.
const REFERENCE = { width: 1600, height: 1000 };

/** Top-to-bottom when it fits the reference frame clearly better than left-to-right. */
export function chooseDirection(
  lr: { width: number; height: number },
  tb: { width: number; height: number },
): "RIGHT" | "DOWN" {
  const scale = (l: { width: number; height: number }) => fitScale(l as Layout, REFERENCE.width, REFERENCE.height);
  return scale(tb) > scale(lr) * 1.15 ? "DOWN" : "RIGHT";
}

/**
 * Lay out a graph once per shape (`key`). The direction (left-to-right or top-to-bottom) is
 * picked from the first shape's proportions and then kept for this view, so neither a resize
 * nor steps appearing during a run flips it. While a new shape is being laid out the previous
 * layout keeps rendering, so live updates never flash.
 */
export function useElkLayout(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
  key: string,
  size: { width: number; height: number },
) {
  const cache = useRef(new Map<string, Layout>());
  const dir = useRef<"RIGHT" | "DOWN" | null>(null);
  const [layout, setLayout] = useState<Layout | null>(null);
  const latest = useRef({ nodes, edges, size });
  latest.current = { nodes, edges, size };
  const ready = size.width > 0 && size.height > 0;

  useEffect(() => {
    if (!ready || !key) return;
    const hit = cache.current.get(key);
    if (hit) {
      setLayout(hit);
      return;
    }
    let cancelled = false;
    const { nodes: ns, edges: es } = latest.current;
    const chosen = dir.current;
    const done = chosen
      ? run(ns, es, chosen, key)
      : Promise.all([run(ns, es, "RIGHT", key), run(ns, es, "DOWN", key)]).then(([lr, tb]) => {
          dir.current = chooseDirection(lr, tb);
          return dir.current === "DOWN" ? tb : lr;
        });
    done
      .then((best) => {
        cache.current.set(key, best);
        if (!cancelled) setLayout(best);
      })
      .catch((err) => console.error("elk layout failed", err));
    return () => {
      cancelled = true;
    };
  }, [key, ready]);

  return layout;
}
