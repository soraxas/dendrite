## Why

Every run adds blobs to the store, and nothing ever removes them. There's no way to see how much space each experiment holds, how much is shared cache, or what is safe to delete. Deleting by hand inside `objects/` would corrupt lineage, and cache hits only survive because they re-check that blobs exist. For multi-GB reconstruction outputs, especially on S3, people need to see the footprint and reclaim it safely.

## What Changes

- **Storage inventory (read-only).** `Lab.storage()` and `dendrite du` report:

  - **Store totals:** object count and bytes, split into referenced and *orphaned* (blobs no record points to, e.g. from failed or interrupted runs).
  - **Per experiment:** bytes referenced, and bytes it holds *exclusively*, i.e. what deleting it would free. Shared cache is counted once.
  - **Cache entries** (successful executions): step, output size, how many experiments reuse it, when it was last used, and whether its blobs are present.
  - The largest artifacts, the local checkout cache, and leftover scratch directories from crashed runs.

- **Delete experiments.** `Experiment.delete(forks=False)` removes an experiment's record and node runs. Running experiments are refused, and so are experiments with forks unless `forks=True` (which deletes the subtree). Executions, artifacts and lineage stay, because other experiments may reuse them. Whatever becomes unreferenced is left for garbage collection.

- **Keep flag.** `Experiment.keep(True)` protects an experiment's data from age-based eviction.

- **Evict cache entries.** `lab.evict(execution_or_artifact)` deletes an execution's output blobs but keeps its lineage record. The next run that needs it recomputes, because cache hits already require verified blobs. It refuses when a living experiment *pins* those outputs, since that experiment could no longer run, unless forced.

- **Garbage collection.** `lab.gc(older_than=None, dry_run=True)` deletes:

  - orphaned blobs
  - blobs referenced only by deleted experiments or failed executions
  - with `older_than`, outputs of cache entries unused for that long, unless a kept experiment, or an experiment's newest run, uses them

  Safety rules:

  - The CLI previews by default.
  - Blobs younger than a grace period are never touched, which protects in-flight uploads.
  - GC refuses while any experiment is running, unless forced.

- **Scratch clean-up.** This clears the local checkout cache (it re-materialises on demand) and leftover step workdirs.

- **API and CLI.**

  - Endpoints: `GET /api/storage`; `DELETE /api/experiments/{id}` now covers any non-running experiment (`?forks=1` for a subtree; drafts behave as before); `POST /api/experiments/{id}/keep`; `POST /api/executions/{id}/evict`; `POST /api/gc` (dry run unless `apply`); `POST /api/storage/clean`. Every one that changes something requires editing to be enabled.
  - Commands: `dendrite du | rm | keep | evict | gc | clean`, where `gc` and `rm` preview unless `--yes`.

- **UI.**

  - A **Storage** page with overall totals and three tabs:
    - Experiments: size, what deleting frees, a keep toggle, delete
    - Cache: reuse count, last used, evict
    - Largest artifacts
  - A **Clean up** panel shows the GC preview, including the "unused for N days" option, before you confirm.
  - Experiment details gain their size and a Delete action.

- **Non-goals:**

  - deleting execution or lineage *records* (metadata stays, so history is auditable)
  - storage quotas
  - scheduled or automatic GC
  - S3 lifecycle rules

## Capabilities

### New Capabilities

- `storage-management`: inventory, experiment deletion, keep, eviction, GC and scratch clean-up in the core and CLI.
- `storage-ui`: the Storage page, the clean-up preview, and the delete and size details on experiments.

### Modified Capabilities

- `lineage-ui`: adds requirements for the storage endpoints, and broadens `DELETE /api/experiments/{id}` beyond drafts.

## Impact

- **Code:**
  - `store.py`: object listing with size and modification time, for both backends
  - `db.py`: an `experiment_flags` table (a new table, since `create_all` can't add columns), plus queries for references and last use
  - `experiment.py` / a new `storage.py`: inventory, delete, evict, gc
  - `server.py`, `cli.py`
  - UI: the Storage page
- **Invariants:**
  - Lineage records are never deleted.
  - A cache hit still verifies blobs, so eviction degrades to recomputation and never to a wrong result.
- **Risk:** deleting data that someone still wants. → Previews by default, explicit confirmation showing byte counts, the keep flag, pins block eviction, and a grace period with a running-experiment guard against races.
