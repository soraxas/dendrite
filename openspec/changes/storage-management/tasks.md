## 1. Core

- [x] 1.1 `iter_objects()` on LocalStore and FsspecStore (hash, size, mtime)
- [x] 1.2 `experiment_flags` table; `keep()`; `keep` in summary and tree
- [x] 1.3 Reachability: roots from experiments (inputs, pins, node-run outputs) plus dir members; per-blob reachers; last use per execution
- [x] 1.4 `Lab.storage()` inventory (totals, per experiment, cache entries, largest, scratch)
- [x] 1.5 `Experiment.delete(forks=False)` in one transaction; refusal rules; `discard()` kept for drafts
- [x] 1.6 `lab.evict(target, force, dry_run)` with pin and running guards
- [x] 1.7 `lab.gc(older_than, dry_run, grace, force, hashes)` with keep/newest/pin protections and reports
- [x] 1.8 `lab.clean()` for checkouts and stale workdirs
- [x] 1.9 Tests: exclusive bytes with shared cache; delete keeps fork outputs usable; evicted step recomputes; orphan and grace; keep and newest under age GC; apply exactly the preview; running guard; clean then load; memory/S3 store listing

## 2. API and CLI

- [x] 2.1 `GET /api/storage` (30 s cache, `?refresh=1`); broaden `DELETE /api/experiments/{id}`; keep, evict, gc, clean endpoints, gated by `allow_edit`
- [x] 2.2 `dendrite du | rm | keep | evict | gc | clean`, previewing unless `--yes`
- [x] 2.3 API and CLI tests (403 when editing is off, preview deletes nothing, `rm` refusing forks without `--forks`)

## 3. UI

- [x] 3.1 API types and queries; `#/storage` route; Storage button in the top bar
- [x] 3.2 Storage page: tiles; Experiments, Cache and Largest tabs with keep, delete and evict
- [x] 3.3 Clean-up panel: options, then dry-run preview grouped by reason, then confirm to apply exactly that report
- [x] 3.4 Experiment details: size and exclusive size, Delete with a confirmation listing forks; open the parent afterwards; "deleted experiment" for unknown ids
- [x] 3.5 Vitest tests: byte formatting and grouping, the confirm flows, disabled-when-not-editable

## 4. Docs and verification

- [x] 4.1 README (storage section) and CLAUDE.md (reachability, invariants)
- [x] 4.2 Full pytest, `npm test`, build, CDP screenshots, `openspec validate storage-management --strict`

## 5. Everything from the UI

- [x] 5.1 `Lab.delete(ids, forks=refuse|delete|keep)` re-attaching kept forks to the nearest surviving ancestor in the same transaction; `Experiment.delete(keep_forks=)`; `deletion_estimate`; `mark_stopped`
- [x] 5.2 Endpoints: bulk delete, estimate, stop, artifact evict, `include_recent` for GC, `force` for clean; `dendrite rm --keep-forks`
- [x] 5.3 UI: fork choice (keep by default) with a server estimate; bulk selection and "select old"; Mark stopped; artifact evict; clean up anyway; include recent data
- [x] 5.4 Tests (pytest, Vitest, mutation checks) and a CDP walk-through on a scratch lab

