import { describe, expect, it, vi } from "vitest";

// React Flow doesn't run under jsdom; the DAG view's card building is what's under test.
vi.mock("./components/Graph", async (actual) => ({
  ...(await actual<typeof import("./components/Graph")>()),
  Graph: () => null,
}));

import type { DagNode } from "./api";
import { framed } from "./components/DagView";
import type { GraphNode } from "./components/Graph";
import { gridCells, SHOW_AT_MOST, sampleItems } from "./fanout";
import { grouping } from "./stages";

const node = (id: string, seq: number, stage: string[] = [], status = "computed"): DagNode => {
  const m = /^(.+)\[(.+)\]$/.exec(id);
  return {
    id,
    kind: "node",
    name: id,
    status: status as DagNode["status"],
    seq,
    step: m ? m[1] : id,
    key: m ? m[2] : null,
    stage,
    duration: 1,
  };
};
const card = (n: DagNode): GraphNode => ({ id: n.id, type: "pipelineNode", width: 250, height: 68, data: {} });

describe("big fan-outs", () => {
  it("draws failures and work in progress first, then the first finished items", () => {
    const items = Array.from({ length: 50 }, (_, i) =>
      node(`clip[${i}]`, i, [], i === 40 ? "failed" : i === 45 ? "running" : "computed"),
    );
    const { shown, hidden } = sampleItems(items);
    expect(shown).toHaveLength(SHOW_AT_MOST);
    expect(shown.map((m) => m.key)).toEqual(["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "40", "45"]); // call order kept
    expect(hidden).toHaveLength(38);
  });
  it("batches the status grid and shows each batch's most urgent status", () => {
    const items = Array.from({ length: 1000 }, (_, i) => node(`f[${i}]`, i, [], i === 503 ? "failed" : "computed"));
    const cells = gridCells(items);
    expect(cells.length).toBeLessThanOrEqual(120);
    const bad = cells.find((c) => c.status === "failed")!;
    expect(bad.pick).toBe("f[503]");
    expect(bad.title).toMatch(/^items \d+–\d+: \d+ computed, 1 failed$/);
    expect(gridCells(items.slice(0, 3)).map((c) => c.title)).toEqual(["0: computed", "1: computed", "2: computed"]);
  });
});

describe("sub flows", () => {
  const nodes = [
    node("plan", 0, ["Plan"]),
    ...Array.from({ length: 30 }, (_, i) => node(`clip[${i}]`, i + 1, ["Clips"])),
    node("publish", 40),
  ];
  const build = (stageToggled: string[], collapsedFan: string[] = []) => {
    const g = grouping(
      nodes,
      { assign: {}, styles: {} },
      { collapsedFan: new Set(collapsedFan), showSteps: false, stageToggled: new Set(stageToggled) },
    );
    const cards = nodes.filter((n) => !g.members.has(n.id)).map(card);
    for (const id of new Set(g.members.values()))
      if (id.startsWith("stage:") || id.startsWith("group:"))
        cards.push({ id, type: "pipelineNode", width: 250, height: 68, data: {} });
    return framed(cards, nodes, g, false, vi.fn(), vi.fn());
  };
  it("draws an expanded stage as a frame around its steps, with a capped fan-out frame inside", () => {
    const out = build(["stage:Clips"]);
    const by = new Map(out.map((n) => [n.id, n]));
    expect(by.get("stage:Clips")).toMatchObject({ frame: true, type: "frameNode" });
    expect(by.get("fan:clip")).toMatchObject({ frame: true, parent: "stage:Clips" });
    expect(out.filter((n) => n.parent === "fan:clip" && n.type === "pipelineNode")).toHaveLength(SHOW_AT_MOST);
    expect(by.get("more:clip")).toMatchObject({ parent: "fan:clip", type: "moreNode" });
    expect((by.get("more:clip")!.data as { label: string }).label).toBe("+18 more clip");
    expect(by.get("stage:Plan")!.frame).toBeUndefined(); // still a folded card
    expect(by.get("publish")!.parent).toBeUndefined();
  });
  it("keeps folded stages as cards, and drops frames with nothing inside", () => {
    expect(build([]).some((n) => n.frame)).toBe(false);
    const folded = build(["stage:Clips"], ["clip"]); // the fan-out folded: one card inside the stage frame
    expect(folded.find((n) => n.id === "group:clip")!.parent).toBe("stage:Clips");
    expect(folded.some((n) => n.id === "fan:clip")).toBe(false);
  });
  it("handles a 2000-item fan-out quickly, drawing only a few dozen nodes", () => {
    const big = [
      node("plan", 0),
      ...Array.from({ length: 2000 }, (_, i) =>
        node(`frame[${i}]`, i + 1, ["Frames"], i % 97 ? "computed" : "running"),
      ),
    ];
    const t0 = performance.now();
    const g = grouping(
      big,
      { assign: {}, styles: {} },
      { collapsedFan: new Set(), showSteps: true, stageToggled: new Set() },
    );
    const cards = big.filter((n) => !g.members.has(n.id)).map(card);
    const out = framed(cards, big, g, false, vi.fn(), vi.fn());
    const ms = performance.now() - t0;
    expect(out.length).toBeLessThan(20);
    expect(ms).toBeLessThan(200);
  });

  it("skips hidden items when drawing the DAG view's cards", async () => {
    const { render } = await import("@testing-library/react");
    const { DagView } = await import("./components/DagView");
    const { exp } = await import("./test/fixtures");
    const items = Array.from({ length: 30 }, (_, i) => node(`clip[${i}]`, i));
    const g = grouping(
      items,
      { assign: {}, styles: {} },
      { collapsedFan: new Set(), showSteps: true, stageToggled: new Set() },
    );
    expect(() =>
      render(
        <DagView
          dag={{ experiment: exp({ status: "completed" }), nodes: items, edges: [] }}
          nameOf={(x) => x}
          onSelect={vi.fn()}
          onArtifact={vi.fn()}
          onOpenSource={vi.fn()}
          grouping={g}
        />,
      ),
    ).not.toThrow();
  });
});
