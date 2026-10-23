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

## 4. Sub flows and scale

- [x] 4.1 Expanded stages and fan-outs are real sub flows: React Flow parent nodes, laid out by ELK's compound layout (children inside, frames sized to fit, edge routes made absolute from their container); the frame header folds it back
- [x] 4.2 Fan-out cards carry a status grid: one cell per item, or per batch showing its most urgent status; a cell opens its item
- [x] 4.3 An expanded fan-out draws at most 12 items (failed, running and queued first) packed in a grid, plus a "+N more" node; items' edges attach to the frame, so a fan-out of any size adds one edge in and one out
- [x] 4.4 The group panel lists every item with status filters, search and paging
- [x] 4.5 `dag()` loads executions in one query (fixed query count however many nodes); live refetches coalesced to two a second
- [x] 4.6 Tests: sampling, grid batching, frame assembly, empty-frame dropping, a 2000-item timing case, query count; browser check with 300 items

