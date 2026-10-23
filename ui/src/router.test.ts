import { describe, expect, it } from "vitest";
import { href, parse, splitSel, type Route } from "./router";

describe("hash routes", () => {
  const cases: Route[] = [
    { view: "dag", exp: "e-1", sel: "node:train" },
    { view: "params", exp: "e-1" },
    { view: "diff", exp: "e-1", other: "e-2" },
    { view: "lineage", artifact: "ab12", exp: "e-1", sel: "exec:x" },
  ];
  it.each(cases)("round-trips %o", (r) => {
    expect(parse(href(r))).toEqual({ sel: undefined, ...r });
  });
  it("falls back to home for unknown paths", () => {
    expect(parse("#/nope").view).toBe("home");
  });
  it("splits selections on the first colon only", () => {
    expect(splitSel("artifact:ab:cd")).toEqual({ kind: "artifact", id: "ab:cd" });
    expect(splitSel(undefined)).toEqual({});
  });
});
