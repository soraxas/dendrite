// One user-facing vocabulary for run statuses, used by nodes, legends, sidebar, panels and diff.
import type { RunStatus } from "./api";

export interface StatusCopy {
  label: string; // short word on nodes and legends
  explain: string; // one sentence for tooltips
}

export const STATUS: Record<RunStatus, StatusCopy> = {
  computed: { label: "Ran", explain: "This step ran in this experiment." },
  cached: {
    label: "Reused",
    explain:
      "An identical run (same code, inputs and parameters) already existed, so its results were reused instead of running again.",
  },
  pinned: {
    label: "Frozen",
    explain: "Outputs were fixed to another experiment's results on purpose, so this step was skipped.",
  },
  running: { label: "Running", explain: "This step is running now." },
  waiting: {
    label: "Waiting",
    explain: "Another experiment is running this identical step; this one will reuse its result when it finishes.",
  },
  queued: { label: "Queued", explain: "This step has been scheduled and will start once its inputs are ready." },
  failed: { label: "Failed", explain: "This step raised an error. Select it to see the message." },
};

export const statusLabel = (s: string) => (s in STATUS ? STATUS[s as RunStatus].label : s);

export const EXPERIMENT_STATUS: Record<string, string> = {
  draft: "Draft",
  created: "Not run yet",
  running: "Running",
  completed: "Done",
  failed: "Failed",
};

// Draft mode: what running the draft now would do, per step.
export const PLAN: Record<"reuse" | "run" | "frozen", StatusCopy & { tone: string }> = {
  reuse: {
    label: "Reused",
    tone: "cached",
    explain: "An identical run already exists, so this step won't run again — its result is reused for free.",
  },
  run: {
    label: "Will run",
    tone: "planrun",
    explain: "No identical run exists, so this step will run when you run the draft.",
  },
  frozen: {
    label: "Frozen",
    tone: "pinned",
    explain: "Fixed to the parent's result on purpose; this step will be skipped.",
  },
};

export const REASON: Record<string, string> = {
  code: "its code changed",
  params: "its parameters changed",
  inputs: "its inputs changed",
  env: "the environment changed",
  upstream: "an earlier step will run",
  new: "it has never run with these settings",
  missing_blob: "the previous result is missing from storage",
  uncached: "it always runs (not cached)",
};

export const UPSTREAM_NOTE =
  "If the earlier step produces exactly the same output, this step will be reused after all.";
