import { describe, expect, it } from "vitest";
import type { DagEdge, DagNode } from "./api";
import {
  collapsedSteps,
  collapseEdges,
  configInputs,
  controlEdges,
  fanoutGroups,
  memberMap,
  nextSelection,
  progressLine,
  summarize,
  summarizePlan,
} from "./fanout";

const node = (id: string, seq: number, over: Partial<DagNode> = {}): DagNode => {
  const m = /^(.+)\[(.+)\]$/.exec(id);
  return {
    id,
    kind: "node",
    name: id,
    status: "computed",
    seq,
    step: m ? m[1] : id,
    key: m ? m[2] : null,
    duration: 1,
    ...over,
  };
};
const input = (name: string): DagNode => ({ id: `input:${name}`, kind: "input", name, status: "input" });
const edge = (from: string, to: string, label = "output"): DagEdge => ({
  from,
  to,
  label,
  artifact: "h",
  input: label,
});

// settings, video -> plan -> clip[0..4] -> thumb[0..4] -> publish
const clips = [0, 1, 2, 3, 4].map((i) => node(`clip[${i}]`, 2 + i));
const thumbs = [0, 1, 2, 3, 4].map((i) => node(`thumb[${i}]`, 7 + i));
const nodes = [
  input("settings"),
  input("video"),
  node("plan", 0),
  node("model", 1),
  ...clips,
  ...thumbs,
  node("publish", 12),
];
const edges = [
  edge("input:video", "plan", "video"),
  ...clips.map((c) => edge("input:video", c.id, "video")),
  ...clips.map((c, i) => edge(c.id, thumbs[i].id, "clip")),
  ...thumbs.map((t) => edge(t.id, "publish", "thumb")),
];
const reads = [
  { artifact: "s", after: 0, producer: "input:settings" },
  { artifact: "p", after: 1, producer: "plan" },
];

describe("fan-out groups", () => {
  it("groups keyed copies of a step, but not single or plain steps", () => {
    expect(fanoutGroups(nodes).map((g) => [g.step, g.members.length])).toEqual([
      ["clip", 5],
      ["thumb", 5],
    ]);
    expect(fanoutGroups([node("x[0]", 0), node("y", 1)])).toEqual([]);
  });
  it("collapses big groups by default, and a toggle flips one", () => {
    const g = fanoutGroups(nodes);
    expect([...collapsedSteps(g, new Set())]).toEqual(["clip", "thumb"]);
    expect([...collapsedSteps(g, new Set(["clip"]))]).toEqual(["thumb"]);
    const small = fanoutGroups([node("a[0]", 0), node("a[1]", 1)]);
    expect([...collapsedSteps(small, new Set())]).toEqual([]); // two items start expanded
    expect([...collapsedSteps(small, new Set(["a"]))]).toEqual(["a"]);
  });
  it("redirects edges to collapsed groups and drops edges inside a group", () => {
    const members = memberMap(fanoutGroups(nodes), new Set(["clip", "thumb"]));
    const out = collapseEdges([...edges, edge("clip[0]", "clip[1]")], members);
    expect(new Set(out.map((e) => `${e.from}>${e.to}`))).toEqual(
      new Set(["input:video>plan", "input:video>group:clip", "group:clip>group:thumb", "group:thumb>publish"]),
    );
  });
});

describe("group summaries", () => {
  it("leads with failures, then work in progress", () => {
    expect(summarize([node("a[0]", 0), node("a[1]", 1, { status: "failed" })]).status).toBe("failed");
    const s = summarize([
      node("a[0]", 0),
      node("a[1]", 1, { status: "running" }),
      node("a[2]", 2, { status: "cached" }),
    ]);
    expect(s).toMatchObject({ status: "running", done: 2, total: 3 });
    expect(s.line2).toBe("2 of 3 done · ran 1 · reused 1 · 1 running");
  });
  it("counts running items' own progress toward the group's", () => {
    const progress = (done: number) => ({ done, total: 20, unit: "frames", updated_at: 0 });
    const s = summarize([
      node("a[0]", 0),
      node("a[1]", 1, { status: "running", progress: progress(10) }),
      node("a[2]", 2, { status: "running", progress: progress(5) }),
      node("a[3]", 3, { status: "queued" }),
    ]);
    expect(s.fraction).toBeCloseTo((1 + 0.5 + 0.25) / 4);
    expect(s.line2).toBe("1 of 4 done · ran 1 · 2 running · 1 queued · 43%");
  });
  it("adds up the time items took when they ran", () => {
    const s = summarize([node("a[0]", 0, { duration: 2 }), node("a[1]", 1, { status: "cached", duration: 9 })]);
    expect(s).toMatchObject({ status: "computed", line2: "Ran 1 · reused 1", line3: "took 2.0s in total" });
  });
  it("counts a draft's planned outcomes", () => {
    expect(summarizePlan([{ plan: "run" }, { plan: "reuse" }, { plan: "reuse" }])).toMatchObject({
      status: "planrun",
      line2: "Will run 1 · reused 2",
    });
  });
});

describe("pipeline reads", () => {
  it("draws 'decides' from a read result to the first steps called after it", () => {
    const none = new Map<string, string>();
    const out = controlEdges(nodes, edges, reads, none);
    // model and every clip start after the read with no later step feeding them; thumbs and publish don't
    expect(out.map((e) => e.to).sort()).toEqual(["clip[0]", "clip[1]", "clip[2]", "clip[3]", "clip[4]", "model"]);
    expect(out.every((e) => e.from === "plan" && e.label === "decides")).toBe(true);
  });
  it("waits for a queued step's inputs before deciding it is unfed", () => {
    const queued = [...nodes.filter((n) => n.id !== "model"), node("model", 1, { status: "queued" })];
    expect(controlEdges(queued, edges, reads, new Map()).map((e) => e.to)).not.toContain("model");
  });
  it("points at collapsed groups once, and never from a configuring input", () => {
    const members = memberMap(fanoutGroups(nodes), new Set(["clip"]));
    expect(
      controlEdges(nodes, edges, reads, members)
        .map((e) => e.to)
        .sort(),
    ).toEqual(["group:clip", "model"]);
    expect([...configInputs(nodes, reads)]).toEqual(["input:settings"]);
  });
});

describe("progress and selection", () => {
  it("describes progress with time left", () => {
    expect(progressLine({ done: 40, total: 80, unit: "frames" }, 10)).toEqual({
      text: "40/80 frames · ~10s left",
      fraction: 0.5,
    });
    expect(progressLine({ done: 3, total: null, unit: null }, 5)).toEqual({ text: "3" });
    expect(progressLine({ done: 2, total: 100, unit: "files" }, 120).text).toBe("2/100 files · ~98m left");
  });
  it("clicking the selected item again clears the selection", () => {
    expect(nextSelection("node:a", "node:a")).toBeUndefined();
    expect(nextSelection("node:a", "node:b")).toBe("node:b");
    expect(nextSelection(undefined, "node:a")).toBe("node:a");
  });
});
