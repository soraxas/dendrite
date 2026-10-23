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
}
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
  nodes: Map<string, Point>;
  edges: Map<string, { points: Point[]; label?: { x: number; y: number; width: number; height: number } }>;
}

const LABEL_CHAR_W = 6.6;

async function run(nodes: LayoutNode[], edges: LayoutEdge[], dir: "RIGHT" | "DOWN", key: string): Promise<Layout> {
  const ids = new Set(nodes.map((n) => n.id));
  const graph: ElkNode = {
    id: "root",
    layoutOptions: {
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
    children: nodes.map((n) => ({ id: n.id, width: n.width, height: n.height })),
    edges: edges
      .filter((e) => ids.has(e.from) && ids.has(e.to) && e.from !== e.to)
      .map((e) => ({
        id: e.id,
        sources: [e.from],
        targets: [e.to],
        labels: e.label ? [{ text: e.label, width: e.label.length * LABEL_CHAR_W + 10, height: 15 }] : [],
      })),
  };
  const out = await (elk ??= new ELK({ workerUrl })).layout(graph);
  const pos = new Map<string, Point>();
  for (const c of out.children ?? []) pos.set(c.id, { x: c.x ?? 0, y: c.y ?? 0 });
  const routed: Layout["edges"] = new Map();
  for (const e of out.edges ?? []) {
    const s = e.sections?.[0];
    if (!s) continue;
    const l = e.labels?.[0];
    routed.set(e.id, {
      points: [s.startPoint, ...(s.bendPoints ?? []), s.endPoint],
      label: l ? { x: l.x ?? 0, y: l.y ?? 0, width: l.width ?? 0, height: l.height ?? 0 } : undefined,
    });
  }
  return { key, dir, width: out.width ?? 0, height: out.height ?? 0, nodes: pos, edges: routed };
}

function fitScale(l: Layout, w: number, h: number) {
  return Math.min((w - 80) / Math.max(l.width, 1), (h - 80) / Math.max(l.height, 1));
}

// Direction is chosen against this fixed frame, not the window, so resizing never flips it.
const REFERENCE = { width: 1600, height: 1000 };

/** Top-to-bottom when it fits the reference frame clearly better than left-to-right. */
export function chooseDirection(lr: { width: number; height: number }, tb: { width: number; height: number }): "RIGHT" | "DOWN" {
  const scale = (l: { width: number; height: number }) => fitScale(l as Layout, REFERENCE.width, REFERENCE.height);
  return scale(tb) > scale(lr) * 1.15 ? "DOWN" : "RIGHT";
}

/**
 * Lay out a graph once per shape (`key`). The direction (left-to-right or top-to-bottom) is
 * picked from the first shape's proportions and then kept for this view, so neither a resize
 * nor steps appearing during a run flips it. While a new shape is being laid out the previous
 * layout keeps rendering, so live updates never flash.
 */
export function useElkLayout(nodes: LayoutNode[], edges: LayoutEdge[], key: string, size: { width: number; height: number }) {
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
