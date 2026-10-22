## 1. Setup

- [x] 1.1 Scaffold the `uv` package `dendrite` (src layout, Python 3.12) with prefect editable from `../prefect`
- [x] 1.2 Add the deps sqlalchemy, fsspec, fastapi and uvicorn, the extras `postgres` and `s3`, and the dev deps pytest, httpx and moto
- [x] 1.3 Add config resolution for `DENDRITE_HOME`, `DENDRITE_DB_URL`, `DENDRITE_STORE_URL` and `DENDRITE_ENV`

## 2. Artifact store

- [x] 2.1 Add canonical JSON and the sha256 helpers (`hashing.py`)
- [x] 2.2 Add `ArtifactRef` and the `ArtifactStore` interface
- [x] 2.3 Add `LocalStore` with atomic writes, dir manifests, value pickles and hardlinked checkouts
- [x] 2.4 Add `FsspecStore` for `s3://`, `memory://` and similar URLs, with a local materialisation cache
- [x] 2.5 Add `verify(ref)` covering dir manifest members

## 3. Metadata store

- [x] 3.1 Define the SQLAlchemy Core tables: experiments, artifacts, executions, execution_inputs, execution_outputs, metrics, node_runs and leases
- [x] 3.2 Configure the engine: SQLite WAL with busy timeout, and Postgres via psycopg
- [x] 3.3 Add repository methods: an atomic publish, cache lookup, node-run upserts, and leases (acquire, steal, heartbeat, release)
- [x] 3.4 Add recursive CTE ancestors and descendants

## 4. Execution cache and Prefect integration

- [x] 4.1 Add signature computation, with code and env fingerprints and per-node param scoping
- [x] 4.2 Add the `@artifact_task` wrapper: a Prefect task with `NO_CACHE`, call-time `queued` node runs, and `.submit` support
- [x] 4.3 Add the resolve loop: cache hit with verification, lease, double-check, execute, and publish or fail
- [x] 4.4 Add the `workdir()` and `log_metric()` runtime helpers
- [x] 4.5 Add the `@pipeline` decorator: a Prefect flow named after the experiment, plus a markdown summary artifact

## 5. Experiments

- [x] 5.1 Add `Lab`: create, get, list and ingest
- [x] 5.2 Add `Experiment.run()`, recording status and the Prefect flow-run id
- [x] 5.3 Add `fork(params=, pin=, inputs=)` with dotted overrides and pin resolution
- [x] 5.4 Add `diff`, `dag`, `tree` and `metrics` views

## 6. UI and CLI

- [x] 6.1 Add the FastAPI JSON API: experiments, DAG, tree, diff, artifacts, lineage and executions
- [x] 6.2 Add a build-free SPA with a fork tree, a live SVG DAG with layered layout, node, artifact and lineage panels, and a diff view
- [x] 6.3 Add the CLI: list, show, diff, dag (Mermaid), tree, lineage and ui

## 7. Verification

- [x] 7.1 Test fork reuse, the identical-experiment zero-execution case, upstream invalidation, pinning, missing-blob recompute, source-hash change, the concurrent lease, and `cache=False`
- [x] 7.2 Test the FsspecStore against `memory://` and a moto S3 server
- [x] 7.3 Test the API endpoints
- [x] 7.4 Run the Postgres integration test against a throwaway container, skipping it when unavailable
- [x] 7.5 Add the example pipeline `examples/recon.py` and a README quickstart
