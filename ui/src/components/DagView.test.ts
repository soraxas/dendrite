import { describe, expect, it, vi } from "vitest";
import { field, step } from "../test/fixtures";
import { planData, toPorts } from "./DagView";

const nameOf = (id: string) => ({ "e-base": "baseline" })[id] ?? id;

describe("planned step rendering", () => {
  it("shows a param change as the reason a step will run", () => {
    const n = step({ param_schema: [field({ value: 60000, changed: true, overridden: true })] });
    const d = planData(n, "e-draft", nameOf, vi.fn());
    expect(d.status).toBe("planrun");
    expect(d.line2).toBe("Will run · about 14.0m");
    expect(d.line3).toBe("✎ iterations 30000 → 60000");
    expect(d.hover.body).toContain("its parameters changed");
    expect(d.variant).toBe("");
  });

  it("marks steps that only run because of an earlier step as dashed, with the caveat", () => {
    const d = planData(step({ reason: "upstream", estimate: null }), "e-draft", nameOf, vi.fn());
    expect(d.variant).toBe("upstream");
    expect(d.line2).toBe("Will run");
    expect(d.line3).toBe("because an earlier step will run");
    expect(d.hover.body).toContain("reused after all");
  });

  it("links a reused step to the experiment whose result it takes", () => {
    const open = vi.fn();
    const n = step({ plan: "reuse", reason: null, estimate: 2, reuse_from: { execution_id: "x", experiment_id: "e-base", duration: 2 } });
    const d = planData(n, "e-draft", nameOf, open);
    expect(d.status).toBe("cached");
    expect(d.source?.label).toBe("baseline");
    d.source?.open();
    expect(open).toHaveBeenCalledWith("e-base", "train");
  });

  it("does not offer a link when the result came from this experiment", () => {
    const n = step({ plan: "reuse", reason: null, reuse_from: { execution_id: "x", experiment_id: "e-draft", duration: 1 } });
    expect(planData(n, "e-draft", nameOf, vi.fn()).source).toBeUndefined();
  });

  it("uses the frozen colour for pinned steps", () => {
    expect(planData(step({ plan: "frozen", reason: null }), "e-draft", nameOf, vi.fn()).status).toBe("pinned");
  });
});

describe("data chips", () => {
  it("leave out placeholders for outputs that have not been computed", () => {
    const ports = toPorts({
      model: { hash: "plan:train.model", kind: "pending" as never, size: 0, filename: null },
      features: { hash: "ab", kind: "value", size: 2048, filename: null },
      missing: null,
    });
    expect(ports).toEqual([{ name: "features", hash: "ab", sub: "value · 2.0 KB" }]);
  });
});
