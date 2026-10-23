## Why

Watching a run was coarse. The page polled every second, and a long step showed only "running" until it finished: a 12 s blur gave no sign of how far it had got. Real pipelines also fan out, for example one clip per defect across 110 defects, which floods the graph with near-identical cards. And two kinds of dependency had no arrow at all:
- **A configuring input.** The pipeline body reads a settings file and passes its values into steps.
- **A planning step.** The body reads a step's result (a segment plan) and uses it to decide which steps to create.

Both appeared as isolated nodes.

## What Changes

- **Live progress.**
  - `dendrite.progress(done, total, unit)` inside a step records progress against its execution, throttled to 4 writes/s.
  - The step's card shows a bar plus "Running · 40/85 frames · ~6s left", and a fan-out group counts its items' partial progress.
- **Push updates.**
  - `GET /api/events` streams Server-Sent Events naming what changed. Detection uses a cheap database fingerprint, because runs usually happen in another process.
  - The UI refetches only the named experiments. While connected, polling drops to a 15 s safety net, and a header indicator shows "live" or "polling".
- **Collapsible fan-out.**
  - `step.named(key)` copies become one card, `step ×N`, with a combined status and progress.
  - Groups of 4 or more start collapsed. Each card has an expand/collapse control, and the group's panel lists its items.
- **Pipeline reads.**
  - `lab.load` inside a pipeline body (or the new `dendrite.load`) is recorded.
  - An input the body read is marked "configures this run" (dashed outline).
  - A step whose result the body read gets dashed "decides" edges to the first steps called after the read.
- **Re-click to deselect.** Clicking the selected node again clears the selection and its data chips.

## Capabilities

### New Capabilities
- `live-updates`: step progress and pushed change events.

### Modified Capabilities
- `lineage-ui`: fan-out groups, pipeline-read edges, re-click deselection, live indicator.

## Impact

- **Python.**
  - New tables `pipeline_reads` and `execution_progress`, added by `create_all`.
  - `runtime.progress/load/note_read`, and `Lab.load` records body reads.
  - A running node run now carries its `execution_id` from the start.
  - DAG and plan nodes gain `step`, `key` and `progress`; responses gain `reads`. These API changes are additive.
- **UI.** `fanout.ts` (pure grouping, summaries, edges), `useLiveUpdates`, group cards, a group detail panel and dashed edges.
- **data-pipeline.** The workflow uses `dendrite.load`, and blur reports per-frame progress.
