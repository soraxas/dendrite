import { describe, expect, it } from "vitest";
import { fmtDur, fmtVal } from "./format";

describe("fmtVal", () => {
  it("shows floats compactly instead of padding to five significant digits", () => {
    expect(fmtVal(0.001)).toBe("0.001");
    expect(fmtVal(27.5)).toBe("27.5");
    expect(fmtVal(1 / 3)).toBe("0.33333");
  });
  it("keeps integers, strings and missing values readable", () => {
    expect(fmtVal(30000)).toBe("30000");
    expect(fmtVal("exhaustive")).toBe('"exhaustive"');
    expect(fmtVal(null)).toBe("—");
  });
});

describe("fmtDur", () => {
  it("picks a unit by magnitude", () => {
    expect(fmtDur(0.3)).toBe("300ms");
    expect(fmtDur(14)).toBe("14.0s");
    expect(fmtDur(840)).toBe("14.0m");
    expect(fmtDur(null)).toBe("");
  });
});
