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
DENDRITE_TEST_PG_URL=postgresql+psycopg://user:pw@host:port/db uv run pytest tests/test_postgres.py
uv run python examples/recon.py            # demo: baseline + config fork + pinned fork (DEMO_SLEEP=2 to slow nodes)
uv run dendrite ui --port 8765             # API + SPA; also: list | show | diff | dag | tree | lineage
uv build                                   # wheel must contain dendrite/ui/index.html
```

The Postgres tests skip unless `DENDRITE_TEST_PG_URL` is set. Each test creates and drops its own schema. There is no linter or formatter configured.

Configuration env vars:

- `DENDRITE_HOME` (default `.dendrite`)
- `DENDRITE_DB_URL` (SQLite by default, or `postgresql+psycopg://…`)
- `DENDRITE_STORE_URL` (a directory, or an fsspec URL such as `s3://bucket/prefix`)
- `DENDRITE_ENV`, which is mixed into every signature (for example an image digest)

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
5. **`server.py` + `ui/index.html`.**
   - A FastAPI JSON API (`/api/experiments`, `/api/experiments/{id}/dag`, `/api/diff`, `/api/artifacts/{hash}[/lineage|/download]`, `/api/executions/{id}`).
   - A build-free vanilla-JS SPA with its own layered SVG DAG layout. It picks left-to-right or top-to-bottom automatically and polls every 1s while a run is active.
   - There is no npm or bundler. Keep it a single static file.
6. **`viz.py` / `cli.py`.** Mermaid renderers and the argparse CLI.

## Invariants to preserve

- Signatures must never include experiment ids or flow-run ids. That is what makes cross-experiment reuse work.
- Only params a node actually consumes are hashed.
- Only `ArtifactRef`s cross Prefect task boundaries. Never pass large data through Prefect.
- A cache hit must `store.verify()` its outputs, including every member of a directory manifest. A missing blob means recompute.
- Experiment config (params, inputs, pins) is frozen at creation. Re-running only replaces node_runs.

## Known limitations

- **In-process only.** Tasks find their experiment through the in-process `runtime._RUNS` registry. Prefect deployments, remote workers, and process, Dask or Ray task runners don't work yet.
- **Narrow code fingerprint.** The fingerprint covers only the node function's own source (`inspect.getsource`). Callers bump `version=` when helper functions change.
- **Pickles.** `value` artifacts are pickles, and `load()` and the UI preview unpickle them.

## Tests

- `tests/toy.py` defines the toy pipelines and a `CALLS` counter of real executions. Tests assert on that counter to prove cache reuse.
- Pipelines and tasks are registered by name at import time (`_PIPELINES`), so `Experiment.pipeline` needs the defining module imported.
- `conftest.py` starts one session-wide `prefect_test_harness`.

## OpenSpec

Spec-driven changes live in `openspec/`:

- `openspec/config.yaml` holds project context.
- `openspec/changes/add-artifact-lineage/` holds the initial proposal, design, specs and tasks. It is not yet archived, so `openspec/specs/` is empty until `openspec archive add-artifact-lineage` is run.
- Use `/opsx:propose`, `/opsx:apply` and `/opsx:archive` for new work.
- Validate with `openspec validate <change>`.
