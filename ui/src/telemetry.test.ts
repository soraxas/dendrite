import { describe, expect, it } from "vitest";
import { collapseCR, downsample, groupScalars, parseAnsi, smooth } from "./telemetry";

describe("parseAnsi", () => {
  it("styles coloured and bold text and resets", () => {
    const spans = parseAnsi("plain \x1b[1;31mred bold\x1b[0m back");
    expect(spans).toEqual([
      { text: "plain ", fg: undefined, bold: false },
      { text: "red bold", fg: "#c33", bold: true },
      { text: " back", fg: undefined, bold: false },
    ]);
  });

  it("drops non-colour escape sequences", () => {
    expect(
      parseAnsi("a\x1b[2Kb")
        .map((s) => s.text)
        .join(""),
    ).toBe("ab");
  });

  it("handles 256 and true colour", () => {
    expect(parseAnsi("\x1b[38;5;196mx")[0].fg).toBe("rgb(255,0,0)");
    expect(parseAnsi("\x1b[38;2;1;2;3mx")[0].fg).toBe("rgb(1,2,3)");
  });
});

describe("collapseCR", () => {
  it("keeps the last rewrite of a progress bar", () => {
    expect(collapseCR("start\n 10%\r 50%\r100%\ndone")).toBe("start\n100%\ndone");
  });

  it("ignores a trailing carriage return", () => {
    expect(collapseCR("windows line\r\nnext")).toBe("windows line\nnext");
  });
});

describe("groupScalars", () => {
  it("puts title/series keys on one chart and plain keys on their own", () => {
    const charts = groupScalars([
      { key: "loss/train", step: 1, value: 1 },
      { key: "loss/val", step: 1, value: 2 },
      { key: "loss/train", step: 0, value: 3 },
      { key: "score", step: 0, value: 9 },
    ]);
    expect(charts.map((c) => c.title)).toEqual(["loss", "score"]);
    expect(charts[0].series.map((s) => s.name)).toEqual(["train", "val"]);
    expect(charts[0].series[0].points.map((p) => p.step)).toEqual([0, 1]);
  });
});

describe("smooth", () => {
  it("is the identity at 0 and debiased otherwise", () => {
    expect(smooth([1, 2, 3], 0)).toEqual([1, 2, 3]);
    const s = smooth([5, 5, 5], 0.9);
    for (const v of s) expect(v).toBeCloseTo(5);
  });
});

describe("downsample", () => {
  it("keeps spikes while bounding the point count", () => {
    const pts = Array.from({ length: 10000 }, (_, i) => ({ step: i, value: i === 5000 ? 100 : 0 }));
    const out = downsample(pts, 100);
    expect(out.length).toBeLessThanOrEqual(200);
    expect(out.some((p) => p.value === 100)).toBe(true);
  });
});
