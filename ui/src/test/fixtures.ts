// Small builders for API-shaped test data.
import type {
  ExperimentSummary,
  Inventory,
  ParamField,
  Plan,
  PlanNode,
  ReclaimReport,
  StorageExperiment,
} from "../api";

export const exp = (over: Partial<ExperimentSummary> = {}): ExperimentSummary => ({
  id: "e-draft",
  name: "try",
  parent_id: "e-base",
  pipeline: "toy",
  status: "draft",
  params: {},
  inputs: {},
  pins: {},
  created_at: 1_700_000_000,
  started_at: null,
  finished_at: null,
  flow_run_id: null,
  error: null,
  ...over,
});

export const field = (over: Partial<ParamField> = {}): ParamField => ({
  name: "iterations",
  value: 30000,
  default: 30000,
  inherited: 30000,
  overridden: false,
  changed: false,
  annotation: "int",
  json_type: "integer",
  ...over,
});

export const step = (over: Partial<PlanNode> = {}): PlanNode => ({
  id: "train",
  kind: "node",
  name: "train",
  status: "queued",
  plan: "run",
  reason: "params",
  estimate: 840,
  reuse_from: null,
  param_schema: [],
  inputs: {},
  outputs: {},
  ...over,
});

export const plan = (over: Partial<Plan> = {}): Plan => ({
  experiment: exp(),
  nodes: [],
  edges: [],
  complete: true,
  blocked_by: null,
  error: null,
  env: {},
  module: "my.pipelines",
  totals: { steps: 4, run: 2, reuse: 2, frozen: 0, estimate: 840, unestimated: 0 },
  ...over,
});

export const storageExp = (over: Partial<StorageExperiment> = {}): StorageExperiment => ({
  id: "e-base",
  name: "base",
  pipeline: "toy",
  status: "completed",
  parent_id: null,
  keep: false,
  created_at: 1_700_000_000,
  finished_at: 1_700_000_100,
  forks: [],
  bytes: 5 << 20,
  exclusive: 2 << 20,
  with_forks: 2 << 20,
  ...over,
});

export const inventory = (over: Partial<Inventory> = {}): Inventory => ({
  computed_at: Date.now() / 1000,
  store: {
    url: "/lab/store",
    objects: 12,
    bytes: 9 << 20,
    live: { objects: 10, bytes: 8 << 20 },
    shared: { objects: 4, bytes: 3 << 20 },
    collectable: { objects: 2, bytes: 1 << 20 },
  },
  experiments: [storageExp()],
  cache: [],
  largest: [],
  scratch: { checkouts: 1024, tmp: 0 },
  busy: [],
  ...over,
});

export const report = (over: Partial<ReclaimReport> = {}): ReclaimReport => ({
  dry_run: true,
  deleted: [
    { hash: "a".repeat(64), size: 1000, why: "orphan" },
    { hash: "b".repeat(64), size: 3000, why: "unreferenced" },
    { hash: "c".repeat(64), size: 2000, why: "unused-since:2026-09-01" },
  ],
  bytes: 6000,
  skipped: [{ hash: "d".repeat(64), size: 10, why: "grace" }],
  groups: {
    "unused-since": { count: 1, bytes: 2000 },
    unreferenced: { count: 1, bytes: 3000 },
    orphan: { count: 1, bytes: 1000 },
  },
  ...over,
});
