## Context

The store is content-addressed (`objects/<h[:2]>/<h[2:]>`), with directories stored as manifest blobs that list their member hashes. Metadata ties blobs to meaning:
- `artifacts` registers refs
- `execution_outputs` / `execution_inputs` connect artifacts to executions
- `node_runs` connect experiments to executions and outputs
- experiments hold `inputs` and `pins`

A cache hit already requires `store.verify()`, so a missing blob means recompute, never a wrong result. That property is what makes reclaiming space safe.

## Goals / Non-Goals

**Goals:**
- Show where the space goes.
- Delete experiments.
- Free space by eviction and GC without ever corrupting lineage or producing a wrong cache hit.
- Make every destructive action previewable.

**Non-Goals:**
- Deleting metadata rows for executions or artifacts.
- Quotas.
- Background or scheduled GC.
- S3 lifecycle integration.

## Decisions

### D1. Reachability defines what is kept
A blob is **live** when it is reachable from a root:
- an existing experiment's input hashes
- its pin hashes
- the outputs of its node runs
- members of the dir manifests of any of the above

Everything else in `objects/` is collectable:
- **Orphans:** blobs with no `artifacts` row, such as uploads from failed or interrupted executions.
- **Unreferenced artifacts:** artifacts no living experiment reaches, e.g. ones only deleted experiments used. Their `artifacts` rows stay with no blob behind them, and lineage shows them as "missing".

Cache entries of deleted experiments are no longer reached, so GC frees them. That is the intended way to "remove old builds": delete the experiments, then GC.

*Alternative:* reference counting on every write. Rejected, because it is error-prone under crashes, while a mark-and-sweep over metadata is simple and correct.

### D2. Age-based eviction is opt-in and respects keep and newest runs
`gc(older_than=Δ)` also evicts the outputs of executions whose *last use* is older than now − Δ. Last use is the newest `finished_at` of any node_run resolving to that execution. Such outputs are spared when they are reached from:
- an experiment flagged keep
- the newest completed experiment of each pipeline
- any pin

Evicted outputs simply recompute when a run needs them again.

### D3. Exclusive bytes per experiment
For the inventory, each live blob's reachers are counted: the set of experiments reaching it.
- **Exclusive bytes:** blobs with exactly one reacher. This is what deleting that experiment and then running GC would free.
- **Shared bytes:** blobs with several reachers.
- A dir counts its manifest plus its members, each member once.

The computation is one pass over node_runs and experiments plus manifest reads, which is cheap locally. On S3, manifest reads are cached per hash in the local cache dir, and the inventory result is cached for 30 s in the server.

### D4. Store listing
`ArtifactStore.iter_objects() -> Iterator[(hash, size, mtime)]`:
- `LocalStore` walks `objects/`.
- `FsspecStore` uses `fs.find(root + "/objects", detail=True)` (S3 `LastModified`, `size`).

`delete()` already exists. The sweep deletes in batches, and on fsspec it uses `fs.rm(list)`.

### D5. Safety against races
A step uploads its outputs before `publish` registers them, so a fresh blob can look orphaned for a moment.
- **Grace period:** GC and orphan deletion skip blobs with `mtime > now − grace`, where `grace` defaults to 1h (`DENDRITE_GC_GRACE`).
- **Running guard:** GC and evict refuse while any experiment is `running` or any lease is held, unless `force=True`. A forced action can at worst make a running step fail to read an input; it can never produce a wrong reuse.
- **Pins:** evicting outputs that a living experiment pins is refused without force, because that experiment could no longer run (its pin would point at a missing blob).

### D6. Deleting experiments
`Experiment.delete(forks=False)`:
- **Refusals:** a `running` experiment is refused. One with children raises unless `forks=True`, which deletes the subtree bottom-up.
- **One transaction:** it removes the experiment's `node_runs`, `experiment_flags` and `experiments` rows.
- **What stays:** executions keep their `experiment_id` (the experiment that *triggered* them). The UI shows "deleted experiment" for unknown ids, so lineage stays readable.
- **Discard:** `discard()` remains as the draft-only alias.

### D7. Keep flag in a new table
`experiment_flags(experiment_id PK, keep BOOL, updated_at)`. A new table is needed because `create_all` can't add a column to `experiments`. It is read through `Lab.tree()` and summary as `keep`.

### D8. Reports
`gc()` and `evict()` return `{deleted: [{hash, size, why}], bytes, skipped: [{hash, why}], dry_run}`. `why` is one of:
- `orphan`
- `unreferenced`
- `unused-since:<iso date>`
- `evicted:<execution id>`

Skip reasons are `grace`, `kept`, `pinned` and `newest`. The UI's clean-up panel renders a dry-run report, and "Delete N items (X GB)" applies exactly that report's hashes, via `apply` plus a hash list echoed back, so nothing new sneaks in between preview and confirmation.

### D9. Scratch clean-up
`lab.clean()` removes:
- `cache/checkouts/*` (hardlinks or copies; they re-materialise on demand)
- `tmp/<exec id>` directories whose execution isn't `running`, or that are older than the grace period

It never touches `objects/`.

### D10. UI
- **Top bar:** a "Storage" button next to "Lab" opens `#/storage`.
- **Storage page:**
  - **Tiles:** total, live, shared cache, collectable, scratch.
  - **Experiments tab:** sortable by size or exclusive bytes; keep toggle; Delete with a confirmation stating "frees ~X after cleanup".
  - **Cache tab:** step, size, reused by N, last used, present; Evict.
  - **Largest artifacts tab:** links to lineage.
  - **Clean up panel:** an "also evict unused for [N] days" control, then a dry-run preview grouped by reason, then confirm.
- **Experiment details:** show "size: X (Y only here)" and a Delete action. Deleting navigates to the parent.
- **Gating:** all mutating controls follow `info.edit`.

## Risks / Trade-offs

- **The inventory can be slow on S3 with millions of objects.** → Cache the result and refresh on demand. The listing is paginated by fsspec, and the page shows when it was computed.
- **The keep flag and "newest run" protection might still evict something wanted.** → A dry run is the default everywhere. Evicted data is recomputable, but not free.
- **Deleting a fork's parent with `forks=True` removes descendants a user may not see.** → The confirmation lists them.

## Migration Plan

`create_all` adds `experiment_flags`. Nothing else changes, and existing data is untouched until someone runs GC.

## Open Questions

- Should `rm` also run GC on what it freed? Proposed: no. Keep the two steps separate, and the UI offers "Clean up now" after a delete.
