## Decisions

### D1. A stage is a function (or a block)
`@stage("Privacy")` wraps a function; `with stage("COLMAP"):` wraps lines. Both push onto a context variable that `_enqueue`/`_plan` read when a step is called, so a step's stage is where it is *called*, not where it is defined: the same step can appear in two stages. Functions make stages composable: they nest, they're reused across pipelines, and they're ordinary testable code. `@artifact_task(stage=...)` is only a fallback for steps called outside any stage.

### D2. Display only
Stages never enter a signature, so regrouping a pipeline never recomputes anything. They're stored per experiment (`node_stages`) because the body decides them at run time, and they're cleared on rerun like node runs.

### D3. The UI view is an overlay
`pipeline_views` holds `{assign: {step: stage}, styles: {stage: {label, description, color}}}` per pipeline. The effective stage of a node is:
1. its code path, if it has one
2. otherwise the view's assignment for its step
3. otherwise none

Styles relabel and recolour both code and UI stages. The view never edits code; "copy as code" offers a snippet to paste instead. Code stays the source of truth wherever it says something.

### D4. Grouping reuses the fan-out machinery
A stage card is a group like `cut_clip ×3`: a member map redirects edges, merges duplicates and aggregates status. A node belongs to the outermost collapsed stage on its path, or failing that to its collapsed fan-out group. Stages start collapsed, and toggles flip that per experiment.

### D5. Colour
A stage takes its `color` (one of the named palette tones) or a stable hash of its name into the palette, so the same stage keeps its colour across experiments.
