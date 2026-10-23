## Why

Real pipelines have a dozen or more steps, and many more with fan-out, but people think about them in a handful of phases: privacy, frames, reconstruction, point cloud, publish. The graph shows only individual steps, so there's no high-level picture, and nothing in the code says which steps belong together.

## What Changes

- **Stages in code.** `dendrite.stage(name, description=None, color=None)` works two ways:
  - as a decorator on an ordinary function, so calling the function puts every step it calls in that stage
  - as a context manager (`with stage(...)`) inside a pipeline body

  Stages nest. `@artifact_task(stage=...)` gives a default for steps called outside any stage. Stages are display-only and never enter a signature.
- **Recorded and parsed.**
  - Each node run records its stage path, and DAG and plan nodes carry it.
  - The static shape recognises `with stage(...)` blocks and `@stage` helper functions, so drafts and the Code view group by stage before anything runs.
- **Overview in the UI.** Each stage is one card with its name, description, colour, a combined status and progress, and its step count. Edges between stages are merged.
  - Stages start collapsed (the overview). Each one expands in place, and nested stages show as cards inside.
  - A toolbar switch flips between the overview and all steps.
- **Managed in the UI.** A per-pipeline *view*, stored in the database and never in code, can:
  - assign ungrouped steps to a stage
  - rename, describe or recolour stages, whether code-defined or UI-only

  "Copy as code" turns the view into `with stage(...)` lines. Code-defined stages always win.

## Capabilities

### New Capabilities
- `pipeline-stages`: stage declaration, recording, view overlays and the stage overview.

## Impact

- **Python.**
  - New tables `node_stages` (per experiment node) and `pipeline_views` (per pipeline), added by `create_all`.
  - `runtime.stage` and `ArtifactTask(stage=)`.
  - `stage` added to DAG, plan and shape nodes.
  - `GET`/`PUT /api/pipelines/{name}/view`.
- **UI.** `stages.ts` (effective stage, grouping, snippet), stage cards, stage detail and editing, overview switch.
- **data-pipeline.** The workflow body is grouped into stages.
