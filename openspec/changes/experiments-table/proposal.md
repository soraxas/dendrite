## Why

Experiments are listed as a fork tree in the sidebar, and you can compare two at a time. Once there are dozens of runs, the questions are "which run had the best PSNR?", "what do all the lr sweeps look like side by side?" and "show me only the ablations". ClearML's main screen, a table of runs with the columns you choose plus multi-select compare, answers them, and it's the largest remaining gap for browsing results.

## What Changes

- **Experiments table** (`#/experiments`, the new default for the Experiments tab once a project has more than a few runs):
  - one row per experiment, showing name, status, pipeline, parent, created, duration, size and tags
  - **columns you choose:** any parameter (`train.iterations`), or any metric as its last, min or max value per step (`evaluate.psnr (last)`); the chosen columns are saved per pipeline in the browser
  - sort by any column, filter by text, status, pipeline, tag, or a parameter or metric range
  - multi-select rows, then **Compare**
- **N-way compare** (`#/compare?exps=a,b,c`), for up to 8 experiments:
  - **Parameters:** a table with one column per experiment, showing only differences by default
  - **Metrics:** each metric's last value per experiment, with the best highlighted (direction per metric, guessed from the name: `loss` lower, `psnr`/`acc` higher; overridable)
  - **Charts:** every step's scalars overlaid across the selected experiments (reusing `mergeSources`)
  - **Outputs:** pick a `node.output` to see it side by side (reusing Compare)
- **Experiment tags and notes:**
  - free-form tags (`exp.tag("ablation")`, `dendrite tag EXP ablation`, and in the UI)
  - a markdown note per experiment
  - both are metadata in a new `experiment_meta` table, never config, so experiments stay frozen
- **Archive:** hide experiments from the default views without deleting them (`archived` in `experiment_meta`), with an "include archived" filter. Deletion and clean-up are unchanged.
- **API:**
  - `GET /api/experiments/table?metrics=…` (one query: experiments, flattened params, metric summaries and tags)
  - `PUT /api/experiments/{id}/meta` (tags, note, archived)
  - `GET /api/compare/experiments?exps=…`
- **Non-goals:** server-side saved views shared across a team (start with the browser's local storage), hyperparameter-search tooling, and custom chart dashboards.

## Capabilities

### New Capabilities

- `experiments-table`: the table, column selection and filters, N-way compare, experiment tags, notes and archive.

### Modified Capabilities

- `lineage-ui`: the Experiments tab gains the table view; the sidebar tree stays for navigation.

## Impact

- **`db.py`:** a new `experiment_meta` table, and metric summaries per execution (last/min/max) computed in SQL.
- **`experiment.py`:** `Experiment.tag/untag/note/archive`.
- **`server.py`, `cli.py`:** the new endpoints, plus `dendrite tag`, `dendrite list --tag/--archived`.
- **Hosted:** meta writes need the runner role and ownership (or admin), like other experiment changes.
- **UI:** new `components/ExperimentsTable.tsx` and `components/CompareMany.tsx`, plus routing.
