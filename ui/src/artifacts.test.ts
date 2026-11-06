import { describe, expect, it } from "vitest";
import type { ArtifactRef, Dag, DagNode } from "./api";
import { artifactRows, compareCandidates, filterRows, groupRows } from "./artifacts";
import { href, parse } from "./router";
import { exp } from "./test/fixtures";

const ref = (hash: string, filename: string | null, kind: ArtifactRef["kind"] = "file"): ArtifactRef => ({
  hash: hash.padEnd(64, "0"),
  kind,
  size: 10,
  filename,
});
const node = (over: Partial<DagNode>): DagNode =>
  ({ id: over.name!, kind: "node", status: "computed", ...over }) as DagNode;

const dag: Dag = {
  experiment: exp({ id: "e-me", status: "completed" }),
  edges: [],
  names: { ["b".padEnd(64, "0")]: ["best"] },
  nodes: [
    { id: "input:video", kind: "input", name: "video", status: "input", artifact: ref("a", "clip.mp4") },
    node({
      name: "colmap",
      seq: 1,
      status: "cached",
      execution_experiment: "e-parent",
      outputs: { sparse: ref("s", null, "dir") },
    }),
    node({
      name: "train",
      seq: 2,
      stage: [{ name: "Training" }],
      outputs: { model: ref("b", "splats.ply"), render: ref("c", "r.png") },
    }),
    ...[0, 1, 2, 3, 4].map((k) =>
      node({
        name: `tile[${k}]`,
        step: "tile",
        key: String(k),
        seq: 3 + k,
        outputs: { img: ref(`d${k}`, `t${k}.jpg`) },
      }),
    ),
    node({ name: "later", seq: 10, status: "running", outputs: {} }),
  ],
};

describe("artifactRows", () => {
  it("lists inputs then outputs in pipeline order with their source and names", () => {
    const rows = artifactRows(dag);
    expect(rows.map((r) => r.id).slice(0, 4)).toEqual(["input:video", "colmap.sparse", "train.model", "train.render"]);
    const sparse = rows.find((r) => r.id === "colmap.sparse")!;
    expect([sparse.source, sparse.from, sparse.kind]).toEqual(["reused", "e-parent", "directory"]);
    const model = rows.find((r) => r.id === "train.model")!;
    expect([model.source, model.kind, model.names, model.stage]).toEqual(["computed", "3d", ["best"], "Training"]);
    expect(rows.find((r) => r.id === "input:video")!.kind).toBe("video");
  });

  it("groups by stage and step, counting fan-out items", () => {
    const groups = groupRows(artifactRows(dag));
    expect(groups.map((g) => g.stage)).toEqual(["", "Training"]);
    const tile = groups[0].steps.find((s) => s.step === "tile")!;
    expect([tile.items, tile.rows.length]).toEqual([5, 5]);
  });

  it("filters by kind and by step, output or name", () => {
    const rows = artifactRows(dag);
    expect(filterRows(rows, "image").map((r) => r.id)).toEqual([
      "train.render",
      "tile[0].img",
      "tile[1].img",
      "tile[2].img",
      "tile[3].img",
      "tile[4].img",
    ]);
    expect(filterRows(rows, undefined, "best").map((r) => r.id)).toEqual(["train.model"]);
    expect(filterRows(rows, undefined, "COLMAP").map((r) => r.id)).toEqual(["colmap.sparse"]);
  });
});

describe("compareCandidates", () => {
  it("takes this experiment, its parent and its forks, capped", () => {
    const tree = [
      { id: "p", parent_id: null },
      { id: "me", parent_id: "p" },
      ...Array.from({ length: 9 }, (_, i) => ({ id: `f${i}`, parent_id: "me" })),
      { id: "sibling", parent_id: "p" },
    ];
    const { ids, more } = compareCandidates(tree, "me");
    expect(ids.slice(0, 3)).toEqual(["me", "p", "f0"]);
    expect(ids).toHaveLength(8);
    expect(more).toBe(3);
    expect(ids).not.toContain("sibling");
  });
});

describe("artifacts route", () => {
  it("round-trips filters and the compared output", () => {
    const r = parse("#/exp/e-1/artifacts?sel=artifact%3Aabc&kind=image&q=tile&cmp=train.render");
    expect(r).toEqual({
      view: "artifacts",
      exp: "e-1",
      sel: "artifact:abc",
      kind: "image",
      q: "tile",
      cmp: "train.render",
    });
    expect(parse(href(r))).toEqual(r);
    expect(href({ view: "dag", exp: "e-1", sel: "node:x", kind: "image" })).toBe("#/exp/e-1?sel=node%3Ax");
  });
});
