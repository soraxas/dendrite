## Context

The sidebar tree (`Sidebar.tsx`) and the two-way `DiffPage` are the only ways to browse experiments today. Metrics live per execution in `metrics` (with `metric_times`). An experiment's metrics are its node runs' executions' metrics, and reused steps share them.

## Decisions

### 1. One table query, computed on the server

`GET /api/experiments/table` returns, for every experiment: the summary fields, flattened params (`_flatten`), tags and archived state, and, for each requested metric key, `{last, min, max}` per node.

Metric summaries come from one grouped query over `metrics` joined to `node_runs`: `last` is the value at the maximum step, and `min`/`max` are aggregates. Only the requested metric columns are computed, and the list of available keys comes with the first response. This stays fast at a few thousand experiments without client-side joins.

### 2. Column state lives in the browser, per pipeline

The chosen columns, sort and filters live in `localStorage` keyed by pipeline (wrapped in try/catch, so the defaults apply if storage is unavailable), and are mirrored into the URL so a view can be shared. Server-side saved views can come later.

### 3. Tags, notes and archive are metadata, not config

A new `experiment_meta(experiment_id PK, tags JSON, note TEXT, archived BOOL, updated_at, updated_by)` table, following `experiment_flags`. Config stays frozen and signatures are untouched. Archived experiments are hidden by default in the table and sidebar, still count in storage, and remain reachable by URL.

### 4. Compare-many reuses what exists

- **Parameters:** a generalised `diff_params` across N experiments.
- **Charts:** `mergeSources` over each experiment's `/scalars`.
- **Outputs:** the existing Compare with an explicit list of experiments.
- **Metrics:** a "best" marker per row from a direction rule. Keys matching `loss|err|time|dist|lpips` are lower-is-better; everything else is higher-is-better; both can be overridden per key in the UI.

## Risks

- **Wide tables on small screens.** Sticky first column and horizontal scroll; on phones the table becomes cards.
- **Too many metric keys.** The picker searches and groups by node, and defaults to the latest step's metrics.
