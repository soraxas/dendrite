# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is for

dendrite is an artifact-first experiment lineage and caching layer on top of Prefect 3, in the spirit of DVC plus MLflow. It targets expensive pipelines such as 3D reconstruction, Gaussian splatting and ML training, where researchers fork an experiment, change one parameter, and want everything upstream reused.

Prefect executes the DAG, and dendrite decides which nodes actually need to run:

- Every node execution gets a deterministic **execution signature**.
- An execution whose signature has been seen before, in *any* experiment, reuses those outputs.
- Every artifact's provenance is recorded.
- Experiments are immutable snapshots. They can be forked in two ways:
  - by changing params (a config fork)
  - by pinning an intermediate artifact (an artifact fork)

## Commit and PR rules

- Never add coding-agent attribution to commits, PR descriptions or code. That means no `Co-Authored-By: Claude…` trailers and no "Generated with Claude Code" lines.

## Commands

```bash
uv sync                                    # installs prefect editable from ../prefect (see pyproject [tool.uv.sources])
uv run pytest                              # full suite (Prefect test harness + moto S3 server)
uv run pytest tests/test_lineage.py::test_artifact_fork_pins_node   # single test
uv run pytest -k pin                       # by keyword
DENDRITE_TEST_DB=postgres uv run pytest   # whole suite on Postgres (testcontainers; needs Docker)
uv run python examples/minimal.py          # smallest setup: two steps, a run and a fork
uv run python examples/recon.py            # demo: baseline + config fork + pinned fork (DEMO_SLEEP=2 to slow nodes)
uv run dendrite ui --port 8454 --import examples.recon   # API + SPA; --import enables draft previews
uv run dendrite run <exp> --import examples.recon        # run a draft made in the UI
uv run dendrite check --import examples.recon            # static checks; exit 1 on errors
uv run dendrite du                                       # storage inventory; rm | keep | evict | gc | clean preview unless --yes
                                           # also: list | show | diff | dag | tree | lineage
cd ui && npm run dev                       # hot-reloading UI, /api proxied to :8454 (Node 20+; UI work only)
cd ui && npm run build                     # tsc type-check + vite build into src/dendrite/ui/dist (git-ignored)
cd ui && npm test                          # Vitest + Testing Library (jsdom): UI logic and draft-editing components
scripts/build-ui.sh && uv build            # release: the build guard fails a wheel/sdist without the bundle
```

Postgres for tests comes from the session fixture `pg_url` in `conftest.py`:
- **Source.** `DENDRITE_TEST_PG_URL` if set, otherwise a throwaway `postgres:17-alpine` container (testcontainers, random port).
- **Without Docker.** The tests skip, or fail with `DENDRITE_TEST_PG=require`, which CI sets.
- **Isolation.** `pg_schema_url` gives each test its own schema and drops it afterwards.
- **Whole suite.** `DENDRITE_TEST_DB=postgres` switches the shared `lab` fixture to Postgres. Tests that open a second `Lab` or call the CLI must pass `lab.settings.db_url` / `--db-url` along.

CI is `.github/workflows/ci.yml`: pytest on SQLite and on Postgres, plus the UI tests and build. It clones Prefect into `../prefect` at `PREFECT_SHA`, blobless rather than shallow because versioningit needs the tags; bump it together with your local `../prefect`. There is no linter or formatter configured.

Configuration env vars:

- `DENDRITE_HOME` (default `.dendrite`)
- `DENDRITE_DB_URL` (SQLite by default, or `postgresql+psycopg://…`)
- `DENDRITE_STORE_URL` (a directory, or an fsspec URL such as `s3://bucket/prefix`)
- `DENDRITE_ENV`, which is mixed into every signature (for example an image digest)
- `DENDRITE_CHECK_READS`: `warn` (default), `error` or `off`, for the undeclared-read guard
- `DENDRITE_GC_GRACE`: seconds (default 3600) during which GC skips freshly written objects

## Architecture

The flow of a run spans several modules in `src/dendrite/`:

1. **`experiment.py`:**
   - `Lab` bundles `Metadata` (db) and an `ArtifactStore`.
   - `Experiment.run()` registers a `RunState` in `runtime._RUNS` and calls the pipeline's Prefect flow with `(run_id, input refs)`.
   - `Pipeline._body` sets the `_active_run` contextvar, calls the user function, waits on any `.submit()` futures, and publishes a Prefect markdown summary artifact.
2. **`task.py`, `ArtifactTask`.** Calling a node inside a pipeline runs `_enqueue`, which writes a `queued` node_run. It then invokes a Prefect task (`cache_policy=NO_CACHE`), which runs `_execute`. That method does the following:
   - **Pins:** pinned nodes short-circuit and are recorded as `pinned`.
   - **Splitting arguments:** `_split` separates artifact inputs (any argument containing an `ArtifactRef`, including nested in lists or dicts) from params. Param precedence is experiment `params[node][arg]`, then the call-site value, then the function default.
   - **Signature:** it is `sha256(canonical_json(spec(...)))`.
   - **Resolving:** `_resolve` looks for a verified cache hit, then takes a lease, double-checks the cache, executes under a heartbeat, and calls `db.publish`.
   - **Computing:** `_compute` materialises inputs (`store.load` returns a `Path` or an unpickled value). It then runs the function in a `workdir()`. Returned `Path`s are stored as file/dir artifacts and anything else is pickled as a `value` artifact.
3. **`db.py`.** SQLAlchemy Core tables, with no ORM and no Alembic (`create_all` on connect).
   - The key separation: `experiments` → `node_runs` (per experiment) → `executions` (physical computations shared across experiments) → `execution_inputs` / `execution_outputs` (lineage edges) → `artifacts`.
   - `leases` holds one row per signature that is currently computing.
   - `publish()` commits the execution's success, its edges, metrics, the node_run and the lease release in **one transaction**.
   - SQLite uses two engines: a writer that issues `BEGIN IMMEDIATE`, and a reader. This avoids WAL lock-upgrade `SQLITE_BUSY`.
   - Ancestor and descendant queries are recursive CTEs written in raw SQL that works on both SQLite and Postgres.
4. **`store.py`.** A content-addressed blob store laid out as `objects/<h[:2]>/<h[2:]>`.
   - A directory is a manifest blob, and the directory's hash is that manifest's hash.
   - `LocalStore` uses atomic `os.replace` and hardlinked checkouts. `FsspecStore` handles S3, MinIO, GCS and `memory://`, with a local checkout cache.
5. **`server.py` + `ui/`.**
   - A FastAPI JSON API (`/api/experiments`, `/api/experiments/{id}/dag`, `/api/diff`, `/api/artifacts/{hash}[/lineage|/download]`, `/api/executions/{id}`, `/api/info`). Keep API changes additive, because the legacy page still reads it. The one exception so far: usage `roles` became structured, and the old strings live on as `role_labels`.
   - **Shipping the UI:**
     - `ui/` is a React + TS + Vite app (React Flow + elkjs + TanStack Query).
     - It builds into `src/dendrite/ui/dist/`, which is git-ignored and packaged through `[tool.hatch.build] artifacts`.
     - `hatch_build.py` refuses sdist or wheel builds without the bundle. Editable builds and `DENDRITE_SKIP_UI_CHECK=1` are exempt.
     - `create_app` mounts `dist/` with `StaticFiles` *after* the API routes when the bundle exists. Otherwise it serves the frozen fallback `src/dendrite/ui/legacy.html`; don't add features there.
   - **Layout:**
     - `ui/src/layout.ts` runs ELK in a web worker. It caches positions per graph shape (node ids plus edges), so status polls never re-lay-out.
     - Edges are drawn from ELK's orthogonal routes by the custom `elk` edge in `Graph.tsx`.
   - **Selection** lives in the hash URL (`?sel=node:|input:|artifact:|exec:<id>`). `App.tsx` derives both the graph highlight and the detail panel from it.
   - **Labels:** lineage artifact labels (`label`, `producer_node`, `role`) and structured usage `roles` are computed in Python (`Lab.lineage`, `Metadata.experiments_using_artifact`), not in the UI.
   - **Headless screenshots:** headless Chrome's `--virtual-time-budget` stalls the ELK worker. Use `uv run python scripts/screenshot.py OUT.png URL [wait_s]` instead, which captures in real time over CDP and prints console errors.
6. **`viz.py` / `cli.py`.** Mermaid renderers and the argparse CLI.
7. **Drafts and plans.**
   - **Drafts.** `status='draft'` is the only editable experiment state.
     - `Experiment.update()` / `discard()` and the draft→running transition in `run()` are compare-and-set updates (`Metadata.update_experiment_if`).
     - There is no schema change: `draft` is just a status value.
   - **Plans.** `Experiment.plan()` runs `pipe.fn` directly, without the Prefect flow or `_body`, under a `RunState(plan=True)`.
     - `ArtifactTask.__call__`/`submit` branch to `_plan` *before* `_enqueue`, so nothing is written.
     - `_plan` uses the same `_split`/`spec`/`sha256_json` as `_execute`, then stops at `_valid_hit`.
     - A miss returns placeholder refs (`kind="pending"`, `hash="plan:<node>.<out>"`). Downstream steps become `run`/`upstream`.
     - `store.load` on a placeholder raises `PlanIncomplete`, which the plan reports as `blocked_by`.
   - **Freshness.** `runtime.refresh_modules()` reloads tracked pipeline/step modules whose mtime changed. Without it, a long-lived server would plan with stale code fingerprints.
   - **API.** The draft and plan endpoints live in `server.py`. Planning needs the pipeline module imported (`--import`); otherwise `/plan` answers 409 `pipeline_not_imported`.
   - **Float params.** JSON can't distinguish `1` from `1.0`, so `PATCH` takes a `floats` list. The UI's `components/Draft.tsx` sends float-typed keys in it.
8. **`guard.py`.** The undeclared-read tripwire.
   - It is one permanent `sys.addaudithook`, idle unless the `_active` contextvar is set.
   - `task._compute` sets that contextvar around the step function only. It reports read-only opens of files under `Settings.project_root` (the cwd when the Lab is created), excluding the workdir, `DENDRITE_HOME`, store paths, interpreter prefixes and code files.
   - In `error` mode a violation fails the execution even if the step swallowed the exception.
9. **`analysis.py`.** Static checks over `ast`. The analysed code is never executed and never feeds a signature.
   - **`check_step(task)`** runs from `ArtifactTask.__init__`.
     - Findings are emitted with `warnings.warn_explicit` as `StepDefinitionWarning`, located at the offending file:line, and stored on `task.diagnostics`.
     - `ignore=` filters codes.
     - `task._STEPS` registers every step, for `dendrite check`.
   - **`pipeline_shape(source, resolve)`** is a def-use walk of the pipeline body.
     - `resolve` maps a name to a module global (`fn.__globals__`), looked up lazily, because steps may be defined below the pipeline.
     - `if`/`else` frames mark nodes `conditional`. A step in exclusive arms merges into one node whose conditions are the shared prefix.
     - Loops and comprehensions mark nodes `repeated`.
     - Non-step calls that receive pipeline values become `dynamic` nodes, except builtins and logging.
     - Same-module helpers are expanded one level.
   - **`Pipeline.shape(source=None)`** works on loaded code or unsaved text. Lines are relative to the text; `first_line` locates it in the file.
   - **`Experiment.check()`** compares params and pins against the shape. `unknown-step` drops to a warning when the shape has dynamic regions.
   - **Server.**
     - `/api/pipelines/{name}/shape` (GET for loaded code, POST for unsaved text) calls `runtime.refresh_modules()` first. GET also saves the pipeline's source as a snapshot, so the Code view reuses `GET`/`PUT /api/sources/{hash}` for viewing and saving.
     - Unsaved Code-view edits register in `ui/src/unsaved.ts`, and `App.tsx`'s `navigate` asks before switching view or experiment.
10. **`sources.py`.** Source snapshots of step functions.
   - They live in `code_snapshots` / `execution_sources`, outside the signature, so they never change cache keys.
   - They are recorded when an execution starts.
   - `disk_status` reports `same` only when the snapshot text occurs exactly once in the recorded file.
   - `write_source` writes only to that recorded path. It checks the client's file hash (409 on mismatch) and replaces atomically.
   - `PUT /api/sources/{hash}` is gated by `create_app(allow_edit=...)`. `dendrite ui` enables it on loopback hosts or with `--allow-edit`.

11. **`storage.py`.** Storage accounting and reclamation, exposed as `Lab.storage()/gc()/evict()/clean()` and `Experiment.delete()/keep()`.
   - **Reachability defines what is kept.** Roots are each existing experiment's input hashes, pin hashes and node-run outputs, plus the members of directory manifests. Anything else in `objects/` is collectable. There is no reference counting.
   - **Metadata is never deleted by reclamation.** `Lab.delete(ids, forks=…)` removes only `experiments`, their `node_runs` and `experiment_flags` rows (`Metadata.delete_experiments`, one transaction, children first). With `forks="keep"` it first re-parents surviving forks to the nearest surviving ancestor, in the same transaction. Executions keep their `experiment_id`, which the UI labels "deleted experiment".
   - **Stuck runs.** `Experiment.mark_stopped()` turns a `running` experiment left by a crashed process into `failed`, which unblocks the busy guard.
   - **Safety.** Blobs written within the grace period are skipped. Applying GC or evict, and `clean()`, refuse while an experiment is `running` or a lease is held (`StorageBusy`), unless forced. Eviction refuses pinned outputs (`EvictRefused`). `gc(hashes=…)` intersects a fresh report with the previewed hashes.
   - **Checkouts.** Deleting blobs also drops their checkouts (`store.drop_checkouts`), because local checkouts are hardlinks that would keep the bytes alive.
   - **Age-based GC.** `gc(older_than=…)` treats node-run outputs of executions last used before the cutoff as non-roots. It spares what kept experiments, any pin, or each pipeline's newest completed experiment reach.
   - **Stores.** `ArtifactStore.iter_objects()` lists `(hash, size, mtime)`. `FsspecStore` caches dir manifests locally under `cache/manifests/`.
   - **Server.** The server caches the inventory for 30 s (`?refresh=1`). Every mutation endpoint requires `allow_edit`; previews (`/api/gc`, `/evict` without `apply`) don't. The UI is `ui/src/components/Storage.tsx`, at `#/storage`.

12. **Live graph.**
   - **Progress.** `runtime.progress` writes `execution_progress` rows, throttled by `PROGRESS_INTERVAL`, through `ExecContext.db`. A running node run carries its `execution_id`, so `dag()` can attach `progress`.
   - **Body reads.** `Lab.load` calls `runtime.note_read`, which records `pipeline_reads` when a run is active and no step is executing. Plans keep reads in `RunState.reads` only. `dag()`/`plan()` return them with each read's producer.
   - **Push.** `GET /api/events` is Server-Sent Events. It compares `Metadata.change_tokens()` every `EVENT_INTERVAL`, because runs happen in other processes. `useLiveUpdates` in `ui/src/queries.ts` invalidates only the named experiments, and polling drops to a 15 s safety net while connected.
   - **Grouping.** It is UI-only (`ui/src/fanout.ts`). Nodes named `step[key]` group by `step`, and groups of `COLLAPSE_AT` or more start collapsed.

## Invariants to preserve

- Signatures must never include experiment ids or flow-run ids. That is what makes cross-experiment reuse work.
- The signature's `op` is the step's identity (`ArtifactTask.op`), not its node name. Renamed copies (`step.named(key)`, `with_options(name=...)`) keep it, so fan-out items doing identical work share results. Named copies are created once per key, skip static checks and aren't registered in `_STEPS`.
- Only params a node actually consumes are hashed.
- Only `ArtifactRef`s cross Prefect task boundaries. Never pass large data through Prefect.
- A cache hit must `store.verify()` its outputs, including every member of a directory manifest. A missing blob means recompute.
- Experiment config (params, inputs, pins) is frozen at its first run. Before that, only `draft` experiments are editable. Re-running only replaces node_runs.
- **Reclaiming space never risks a wrong result.** It may only delete blobs, never metadata rows or anything a cache hit could trust without `verify()`. Anything deleted must recompute when needed. `tests/test_storage.py` covers this.
- Progress and body reads are display-only: they never enter a signature.
- **Plans are side-effect free.** A plan never writes node_runs, executions, blobs, leases or pipeline reads, never touches Prefect, and never calls step functions. `tests/test_plan.py::test_plan_writes_nothing` checks this.
- **Step contract** (README, "The step contract"):
  - The pipeline body only wires steps together.
  - Step outputs depend only on what the signature hashes.
  - Params are JSON values, hashed exactly, so `1` and `1.0` give different signatures.

## Known limitations

- **In-process only.** Tasks find their experiment through the in-process `runtime._RUNS` registry. Prefect deployments, remote workers, and process, Dask or Ray task runners don't work yet.
- **Narrow code fingerprint.** The fingerprint covers only the node function's own source (`inspect.getsource`). Callers bump `version=` when helper functions change.
- **Pickles.** `value` artifacts are pickles, and `load()` and the UI preview unpickle them.
- **The read guard is best-effort.** It sees only Python-level `open`/`os.open`. It misses C `fopen`, subprocesses, threads started by the step, and anything outside the project directory.
- **Plans can be incomplete.** They can't predict branching on the *contents* of a result that hasn't been computed. Downstream `run`/`upstream` is an upper bound: an unchanged upstream output still lets downstream reuse.

## Tests

- `tests/toy.py` defines the toy pipelines and a `CALLS` counter of real executions. Tests assert on that counter to prove cache reuse.
- Pipelines and tasks are registered by name at import time (`_PIPELINES`), so `Experiment.pipeline` needs the defining module imported.
- `conftest.py` starts one session-wide `prefect_test_harness`.
- UI tests live next to the code as `ui/src/**/*.test.ts(x)`. Builders are in `ui/src/test/fixtures.ts`, and `renderWithQuery` gives a fresh QueryClient. Mock the network with `vi.spyOn(api, ...)`. `layout.ts` creates the ELK worker lazily, so graph modules import cleanly under jsdom.

## OpenSpec

Spec-driven changes live in `openspec/`:

- `openspec/config.yaml` holds project context.
- `openspec/specs/` holds the baseline specs from the archived `add-artifact-lineage` change.
- `openspec/changes/draft-experiments/` is drafts, plans and the step contract (phase 1 of the editing UI).
- `openspec/changes/storage-management/` is the storage inventory, delete, evict, GC and clean-up change.
- `openspec/changes/live-graph/` is live progress, pushed updates, fan-out groups and pipeline-read edges.
- `openspec/changes/react-flow-ui/` is the React Flow UI change. Archive it with `openspec archive react-flow-ui` once it has been reviewed.
- Use `/opsx:propose`, `/opsx:apply` and `/opsx:archive` for new work.
- Validate with `openspec validate <change>`.
