import { describe, expect, it } from "vitest";
import { anchorNode } from "./Graph";

const at = (o: Record<string, [number, number]>) => new Map(Object.entries(o).map(([k, [x, y]]) => [k, { x, y }]));

describe("anchorNode", () => {
  const before = at({ a: [0, 0], b: [500, 0], f: [480, -20], gone: [490, 0] });
  const after = at({ a: [0, 40], b: [500, 80], f: [480, 60], fresh: [100, 0] });
  const nodes = [{ id: "a" }, { id: "b" }, { id: "f", frame: true }, { id: "gone" }, { id: "fresh" }];

  it("holds the selection still when it survives the re-layout", () => {
    expect(anchorNode(before, after, nodes, "a", { x: 500, y: 0 })).toBe("a");
  });

  it("otherwise picks the surviving plain node nearest the view's centre", () => {
    expect(anchorNode(before, after, nodes, "gone", { x: 490, y: 0 })).toBe("b");
  });

  it("finds nothing when no node is in both layouts", () => {
    expect(
      anchorNode(at({ x: [0, 0] }), at({ y: [0, 0] }), [{ id: "x" }, { id: "y" }], undefined, { x: 0, y: 0 }),
    ).toBe(undefined);
  });
});
