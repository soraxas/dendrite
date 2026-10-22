// Typed client for the dendrite JSON API (src/dendrite/server.py).
export type RunStatus = "queued" | "running" | "waiting" | "computed" | "cached" | "pinned" | "failed";
export type ExperimentStatus = "created" | "running" | "completed" | "failed";
export const RUN_STATUSES: RunStatus[] = ["computed", "cached", "pinned", "running", "waiting", "queued", "failed"];
export const ACTIVE = new Set<string>(["running", "waiting", "queued"]);

export interface ArtifactRef {
  hash: string;
  kind: "file" | "dir" | "value";
  size: number;
  filename: string | null;
}

export interface Info {
  db: string;
  store: string;
  ui: "react" | "legacy";
  edit: boolean;
}

export interface SourceInfo {
  hash: string;
  file: string | null;
  display: string | null;
  line: number | null;
  module: string | null;
  qualname: string | null;
}

export interface SourceSnapshot extends SourceInfo {
  source: string;
  status: "same" | "changed" | "missing";
  file_sha: string | null;
  editable: boolean;
}

export interface TreeEntry {
  id: string;
  name: string | null;
  parent_id: string | null;
  pipeline: string;
  status: ExperimentStatus;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  counts: Partial<Record<RunStatus, number>>;
}

export interface ExperimentSummary {
  id: string;
  name: string | null;
  parent_id: string | null;
  pipeline: string;
  status: ExperimentStatus;
  params: Record<string, Record<string, unknown>>;
  inputs: Record<string, string>;
  pins: Record<string, Record<string, string>>;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  flow_run_id: string | null;
  error: string | null;
}

export interface DagNode {
  id: string;
  kind: "input" | "node";
  name: string;
  status: RunStatus | "input";
  artifact?: ArtifactRef | null;
  seq?: number;
  signature?: string | null;
  execution_id?: string | null;
  execution_experiment?: string | null;
  params?: Record<string, unknown> | null;
  inputs?: Record<string, ArtifactRef | null>;
  outputs?: Record<string, ArtifactRef | null>;
  metrics?: Record<string, number>;
  duration?: number | null;
  started_at?: number | null;
  finished_at?: number | null;
  error?: string | null;
}

export interface DagEdge {
  from: string;
  to: string;
  artifact: string;
  label: string;
  input: string;
}

export interface Dag {
  experiment: ExperimentSummary;
  nodes: DagNode[];
  edges: DagEdge[];
}

export interface Role {
  kind: "input" | "produced" | "read";
  node: string | null;
  name: string;
}

export interface Usage {
  id: string;
  name: string | null;
  roles: Role[];
  role_labels: string[];
}

export interface LineageArtifact extends ArtifactRef {
  label: string;
  producer_node: string | null;
  role: "focus" | "ancestor" | "descendant";
}

export interface LineageExecution {
  id: string;
  node: string;
  signature: string;
  status: string;
  started_at: number;
  finished_at: number | null;
  experiment_id: string | null;
}

export interface Lineage {
  artifact: LineageArtifact;
  producer: string | null;
  ancestors: string[];
  descendants: string[];
  executions: LineageExecution[];
  artifacts: LineageArtifact[];
  edges: { from: string; to: string; label: string }[];
  experiments: Usage[];
}

export interface Execution {
  id: string;
  signature: string;
  node: string;
  spec: Record<string, unknown>;
  cacheable: boolean;
  status: string;
  experiment_id: string | null;
  task_run_id: string | null;
  started_at: number;
  finished_at: number | null;
  error: string | null;
  inputs: Record<string, string>;
  outputs: Record<string, string>;
  metrics: { key: string; step: number; value: number }[];
  used_by?: { experiment_id: string; node: string; status: string; name: string | null }[];
  source?: SourceInfo | null;
}

export type Preview =
  | { type: "text"; text: string }
  | { type: "value"; repr: string }
  | { type: "manifest"; count: number; entries: { path: string; hash: string; size: number }[] }
  | { type: "missing" | "too_large" | "binary" };

export interface ArtifactDetail extends ArtifactRef {
  present: boolean;
  producer: Execution | null;
  experiments: Usage[];
  preview: Preview;
}

export interface Diff {
  a: string;
  b: string;
  params: { key: string; a: unknown; b: unknown; change: "added" | "removed" | "changed" }[];
  inputs: { name: string; a: string | null; b: string | null }[];
  nodes: {
    node: string;
    a_status: string | null;
    b_status: string | null;
    same_signature: boolean;
    same_outputs: boolean;
    same_execution: boolean;
  }[];
  metrics: { node: string; key: string; a: number | null; b: number | null }[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) {
    const body = await r.text();
    let detail = body;
    try {
      detail = JSON.parse(body).detail ?? body;
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, String(detail).slice(0, 300));
  }
  return r.json() as Promise<T>;
}

const get = <T,>(path: string) => request<T>(path);

export const api = {
  info: () => get<Info>("/api/info"),
  experiments: () => get<TreeEntry[]>("/api/experiments"),
  dag: (id: string) => get<Dag>(`/api/experiments/${encodeURIComponent(id)}/dag`),
  diff: (a: string, b: string) => get<Diff>(`/api/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
  artifact: (h: string) => get<ArtifactDetail>(`/api/artifacts/${encodeURIComponent(h)}`),
  lineage: (h: string) => get<Lineage>(`/api/artifacts/${encodeURIComponent(h)}/lineage`),
  execution: (id: string) => get<Execution>(`/api/executions/${encodeURIComponent(id)}`),
  source: (h: string) => get<SourceSnapshot>(`/api/sources/${encodeURIComponent(h)}`),
  saveSource: (h: string, source: string, file_sha: string) =>
    request<{ file_sha: string }>(`/api/sources/${encodeURIComponent(h)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source, file_sha }),
    }),
  downloadUrl: (h: string) => `/api/artifacts/${encodeURIComponent(h)}/download`,
};
