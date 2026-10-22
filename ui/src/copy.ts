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
    explain: "An identical run (same code, inputs and parameters) already existed, so its results were reused instead of running again.",
  },
  pinned: { label: "Frozen", explain: "Outputs were fixed to another experiment's results on purpose, so this step was skipped." },
  running: { label: "Running", explain: "This step is running now." },
  waiting: { label: "Waiting", explain: "Another experiment is running this identical step; this one will reuse its result when it finishes." },
  queued: { label: "Queued", explain: "This step has been scheduled and will start once its inputs are ready." },
  failed: { label: "Failed", explain: "This step raised an error. Select it to see the message." },
};

export const statusLabel = (s: string) => (s in STATUS ? STATUS[s as RunStatus].label : s);

export const EXPERIMENT_STATUS: Record<string, string> = {
  created: "Not run yet",
  running: "Running",
  completed: "Done",
  failed: "Failed",
};
