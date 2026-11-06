## 1. Metadata and API

- [ ] 1.1 `experiment_meta` table; `Metadata.experiment_meta`/`set_experiment_meta`; hosted permissions
- [ ] 1.2 Metric summaries (last/min/max per node and key) in one grouped query
- [ ] 1.3 `GET /api/experiments/table`, `PUT /api/experiments/{id}/meta`, `GET /api/compare/experiments`
- [ ] 1.4 `Experiment.tag/untag/note/archive`; `dendrite tag`, `dendrite list --tag/--archived`

## 2. UI

- [ ] 2.1 `#/experiments` table with default columns, sorting, text, status and pipeline filters
- [ ] 2.2 Column picker for params and metric summaries; per-pipeline persistence and URL mirroring
- [ ] 2.3 Tags, note and archive editing; archived filter in the table and sidebar
- [ ] 2.4 Multi-select and `#/compare?exps=` page: params, metrics with best marker, overlaid charts, outputs
- [ ] 2.5 Phone layout: cards instead of a wide table

## 3. Tests and docs

- [ ] 3.1 API tests (summaries, filters, meta permissions), UI tests (columns, sort, compare)
- [ ] 3.2 README and a docs page for browsing and comparing experiments
