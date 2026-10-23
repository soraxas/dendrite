## 1. Python
- [x] 1.1 `runtime.stage` (decorator + context manager, nesting), `current_stage()`, `ArtifactTask(stage=)` default
- [x] 1.2 `node_stages` table; recorded at enqueue, cleared on rerun/delete; `stage` on DAG and plan nodes
- [x] 1.3 Static shape: `with stage(...)` and `@stage` helpers give shape nodes a `stage` path
- [x] 1.4 `pipeline_views` table; `GET`/`PUT /api/pipelines/{name}/view` (edit-gated, validated)
- [x] 1.5 Tests: recording, nesting, defaults, signatures unchanged, plan, shape, view API

## 2. UI
- [x] 2.1 `stages.ts`: effective stage with view overlay, member map combined with fan-out, colours, code snippet
- [x] 2.2 Stage cards, overview switch, per-stage expand, stage detail with edit (label, description, colour) and assigning steps
- [x] 2.3 Vitest for the logic and the edit flow

## 3. Integration
- [x] 3.1 data-pipeline workflow grouped into stages
- [x] 3.2 Docs, full suites, browser check
