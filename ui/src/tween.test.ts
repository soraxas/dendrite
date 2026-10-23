import { describe, expect, it } from "vitest";
import { blend, ease, resample } from "./tween";

describe("edge morphing", () => {
  it("spaces points evenly along a route, whatever its bends", () => {
    const pts = resample([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }], 5);
    expect(pts).toEqual([{ x: 0, y: 0 }, { x: 5, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 5 }, { x: 10, y: 10 }]);
    expect(resample([{ x: 3, y: 4 }], 3)).toEqual([{ x: 3, y: 4 }, { x: 3, y: 4 }, { x: 3, y: 4 }]);
  });
  it("blends two routes with different numbers of bends", () => {
    const a = resample([{ x: 0, y: 0 }, { x: 0, y: 100 }], 3);
    const b = resample([{ x: 0, y: 0 }, { x: 50, y: 0 }, { x: 50, y: 100 }], 3);
    expect(blend(a, b, 0)).toEqual(a);
    expect(blend(a, b, 1)).toEqual(b);
    expect(blend(a, b, 0.5)[2]).toEqual({ x: 25, y: 100 });
  });
  it("eases in and out, ending exactly at the target", () => {
    expect(ease(0)).toBe(0);
    expect(ease(1)).toBe(1);
    expect(ease(0.5)).toBeCloseTo(0.5);
    expect(ease(0.1)).toBeLessThan(0.1);
  });
});
