import { describe, expect, it } from "vitest";
import type { TreeEntry } from "../api";
import { outcome } from "./Sidebar";

const entry = (status: TreeEntry["status"], counts: TreeEntry["counts"] = {}): TreeEntry => ({
  id: "e-1", name: "x", parent_id: null, pipeline: "toy", status, created_at: 0, started_at: null, finished_at: null, counts,
});

describe("sidebar outcome", () => {
  it("labels drafts as drafts, not as empty experiments", () => {
    expect(outcome(entry("draft"))).toBe("Draft · not run yet");
  });
  it("counts reuse (cached + frozen) against all steps", () => {
    expect(outcome(entry("completed", { cached: 1, pinned: 1, computed: 2 }))).toBe("Reused 2 of 4 steps");
    expect(outcome(entry("completed", { computed: 3 }))).toBe("Ran all 3 steps");
    expect(outcome(entry("running", { cached: 1, running: 1, queued: 1 }))).toBe("Running · 1 of 3 done");
    expect(outcome(entry("failed", { computed: 1, failed: 1 }))).toBe("Failed after 1 of 2 steps");
  });
});
