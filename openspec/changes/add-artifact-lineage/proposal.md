## Why

Expensive pipelines (3D reconstruction, Gaussian splatting, ML training) are re-run over and over while researchers tweak a single downstream parameter. Prefect 3 orchestrates the DAG well, but its caching is per-task and run-scoped by default. It has no notion of an *experiment* that can be forked, and it cannot answer "which inputs, code and parameters produced this artifact?". We need a DVC-style, artifact-first lineage layer on top of Prefect. That layer should reuse any computation whose execution signature has been seen before, across every experiment, and record full provenance.

## What Changes

- New Python package `dendrite` that wraps Prefect 3 tasks and flows. Prefect stays the execution engine. Dendrite decides which computations actually need to run.
- `@artifact_task(outputs=[...], version=...)`: a decorator that computes a deterministic **execution signature**: `H(op, version, code, env, input content hashes, consumed params)`. It looks the signature up in a global cache, takes a lease so concurrent forks don't duplicate work, executes on a miss, and atomically publishes outputs and lineage.
- `@pipeline`: a decorator that turns a function into a Prefect flow bound to an experiment.
- **Experiments** are immutable configuration snapshots. They support:
  - `create`
  - config `fork(params={"train.iterations": 60000})`
  - artifact `fork(pin={"colmap": parent})`
  - `run`
  - `diff`
- A **content-addressed artifact store** with pluggable backends: the local filesystem, or any fsspec object store (S3, MinIO, GCS).
- A **metadata store** via SQLAlchemy Core, supporting SQLite (default, zero-config) and PostgreSQL.
- **Lineage queries**: artifact ancestors and descendants (recursive SQL), the per-experiment DAG, and the experiment fork tree.
- **Live web UI (SPA)** served by a FastAPI app (`dendrite ui`). It shows:
  - the experiment list and fork tree
  - a live DAG with per-node progress (queued, running, computed, cached, pinned, failed)
  - output artifacts
  - metrics
  - parameter diffs
  - artifact provenance
- **CLI** (`dendrite`): `list`, `show`, `diff`, `dag`, `tree`, `lineage`, `ui`.

## Capabilities

### New Capabilities
- `artifact-store`: content-addressed, immutable storage of file, directory and value artifacts. Backends are filesystem and fsspec object stores.
- `metadata-store`: a SQLAlchemy-backed relational store for experiments, executions, artifacts, lineage edges, metrics and leases. Supports SQLite and PostgreSQL.
- `execution-cache`: execution signatures, global cross-experiment cache lookup with blob verification, and lease-based de-duplication of concurrent executions.
- `experiment-lineage`: experiment create, fork (config and artifact pinning), run, diff, and ancestor and descendant queries.
- `prefect-integration`: the `@artifact_task` and `@pipeline` decorators that execute through Prefect 3 and pass only lightweight artifact references between tasks.
- `lineage-ui`: an HTTP API plus a single-page app that tracks DAG progress live and traces outputs and lineage.

### Modified Capabilities
<!-- none: greenfield project -->

## Impact

- New package `src/dendrite/`, tests in `tests/`, and an example pipeline in `examples/`.
- Dependencies:
  - `prefect` (editable, from the local checkout at `../prefect`)
  - `sqlalchemy>=2`, `fsspec`, `fastapi`, `uvicorn`
  - optional extras: `postgres` (psycopg 3) and `s3` (s3fs)
- Configuration via `DENDRITE_HOME`, `DENDRITE_DB_URL`, `DENDRITE_STORE_URL` and `DENDRITE_ENV`.
