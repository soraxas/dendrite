import { describe, expect, it } from "vitest";
import type { DagNode, PipelineView } from "./api";
import { EMPTY_VIEW, grouping, stageColor, stageGroups, stagePath, viewAsCode } from "./stages";

const node = (
  id: string,
  seq: number,
  stage: (string | { name: string; description?: string; color?: string })[] = [],
  over: Partial<DagNode> = {},
): DagNode => {
  const m = /^(.+)\[(.+)\]$/.exec(id);
  return {
    id,
    kind: "node",
    name: id,
    status: "computed",
    seq,
    step: m ? m[1] : id,
    key: m ? m[2] : null,
    stage,
    duration: 1,
    ...over,
  };
};
const nodes = [
  node("extract", 0, [{ name: "Privacy", description: "Sanitised media", color: "teal" }]),
  node("blur[0]", 1, ["Privacy"]),
  node("blur[1]", 2, ["Privacy"]),
  node("colmap", 3, ["Recon", "SfM"]),
  node("train", 4, ["Recon"]),
  node("transcribe", 5),
  node("publish", 6),
];
const view: PipelineView = {
  assign: { transcribe: "Audio" },
  styles: { Audio: { description: "Transcription", color: "purple" }, Recon: { label: "Reconstruction" } },
};

describe("stages", () => {
  it("takes a node's stage from code, then from the view", () => {
    expect(stagePath(nodes[0], view).map((s) => s.name)).toEqual(["Privacy"]);
    expect(stagePath(nodes[5], view).map((s) => s.name)).toEqual(["Audio"]);
    expect(stagePath(nodes[6], view)).toEqual([]);
    expect(stagePath(node("x", 0, ["Code"]), { ...view, assign: { x: "View" } }).map((s) => s.name)).toEqual(["Code"]); // code wins
  });
  it("lists every stage and nested stage with labels, descriptions and colours", () => {
    const g = stageGroups(nodes, view);
    expect(g.map((s) => [s.id, s.name, s.members.length, s.fromView])).toEqual([
      ["stage:Privacy", "Privacy", 3, false],
      ["stage:Recon", "Reconstruction", 2, false],
      ["stage:Recon/SfM", "SfM", 1, false],
      ["stage:Audio", "Audio", 1, true],
    ]);
    expect(g[0]).toMatchObject({ description: "Sanitised media", color: "teal" });
    expect(g[3]).toMatchObject({ description: "Transcription", color: "purple" });
  });
  it("picks a stable colour from the name when none is given", () => {
    expect(stageColor("COLMAP", EMPTY_VIEW)).toBe(stageColor("COLMAP", EMPTY_VIEW));
    expect(stageColor("COLMAP", { assign: {}, styles: { COLMAP: { color: "orange" } } })).toBe("orange");
    expect(stageColor("X", EMPTY_VIEW, "not-a-colour")).not.toBe("not-a-colour");
  });
  it("folds steps into the outermost collapsed stage, else their fan-out group", () => {
    const overview = grouping(nodes, view, {
      collapsedFan: new Set(["blur"]),
      showSteps: false,
      stageToggled: new Set(),
    });
    expect(overview.members.get("blur[0]")).toBe("stage:Privacy"); // stage beats fan-out
    expect(overview.members.get("colmap")).toBe("stage:Recon"); // outermost
    expect(overview.members.get("transcribe")).toBe("stage:Audio");
    expect(overview.members.has("publish")).toBe(false);
    const open = grouping(nodes, view, {
      collapsedFan: new Set(["blur"]),
      showSteps: false,
      stageToggled: new Set(["stage:Privacy", "stage:Recon"]),
    });
    expect(open.members.get("blur[0]")).toBe("group:blur"); // stage open: the fan-out group shows
    expect(open.members.get("colmap")).toBe("stage:Recon/SfM"); // the nested stage is still folded
    expect(open.members.has("train")).toBe(false);
    const steps = grouping(nodes, view, { collapsedFan: new Set(), showSteps: true, stageToggled: new Set() });
    expect(steps.members.size).toBe(0);
  });
  it("offers UI-only stages as code", () => {
    expect(viewAsCode(view)).toBe(
      'with dendrite.stage("Audio", "Transcription", color="purple"):\n    ...  # call transcribe(...) here',
    );
  });
});
