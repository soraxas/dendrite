// Small builders for API-shaped test data.
import type { ExperimentSummary, ParamField, Plan, PlanNode } from "../api";

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
