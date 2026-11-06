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

describe("appendLines", () => {
  it("joins output across chunks and keeps the unfinished line apart", async () => {
    const { appendLines } = await import("./telemetry");
    let buf = { lines: [] as string[], partial: "" };
    buf = appendLines(buf, "a\nb");
    buf = appendLines(buf, "c\n 10%\r");
    buf = appendLines(buf, "99%\ndone");
    expect(buf.lines).toEqual(["a", "bc", "99%"]);
    expect(buf.partial).toBe("done");
  });
});

describe("overlays and axes", () => {
  it("merges sources by title, prefixing series with their source", async () => {
    const { mergeSources } = await import("./telemetry");
    const charts = mergeSources([
      {
        label: "base",
        points: [
          { key: "loss/train", step: 0, value: 1 },
          { key: "psnr", step: 0, value: 30 },
        ],
      },
      { label: "fork", points: [{ key: "loss/train", step: 0, value: 2 }] },
    ]);
    expect(charts.map((c) => c.title)).toEqual(["loss", "psnr"]);
    expect(charts[0].series.map((s) => s.name)).toEqual(["base · train", "fork · train"]);
    expect(charts[1].series.map((s) => s.name)).toEqual(["base"]);
    const single = mergeSources([{ label: "base", points: [{ key: "loss/train", step: 0, value: 1 }] }]);
    expect(single[0].series[0].name).toBe("train");
  });

  it("measures time from each series' first point and drops untimed points", async () => {
    const { xOf, fmtSeconds } = await import("./telemetry");
    const pts = [
      { step: 0, value: 1, t: 100 },
      { step: 1, value: 2, t: null },
      { step: 2, value: 3, t: 160 },
    ];
    expect(xOf(pts, "step").map((p) => p.x)).toEqual([0, 1, 2]);
    expect(xOf(pts, "time").map((p) => p.x)).toEqual([0, 60]);
    expect([fmtSeconds(45), fmtSeconds(200), fmtSeconds(7500)]).toEqual(["45s", "3m 20s", "2h 5m"]);
  });
});
