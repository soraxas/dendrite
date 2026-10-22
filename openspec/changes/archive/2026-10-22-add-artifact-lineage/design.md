## Context

This is a greenfield repo. Prefect 3 (local checkout `../prefect`, 3.9.1.dev1) is the orchestrator. Prefect's default cache policy (`INPUTS + TASK_SOURCE + RUN_ID`) includes the flow-run id, so it cannot share results across experiments. We need DVC-like global reuse and an experiment model like MLflow's, while Prefect keeps doing what it is good at: scheduling, retries, task runners, workers and observability.

## Goals / Non-Goals

**Goals:**
- Keep experiments, node runs, executions and artifacts as separate entities. One execution can back many experiments' node runs.
- Global cache reuse keyed by execution signature. It never includes experiment or flow-run ids.
- Per-node parameter scoping. Changing `train.iterations` must not invalidate `extract` or `colmap`.
- Config forks and artifact forks (pinning an intermediate output).
- Pluggable metadata (SQLite or Postgres) and blob (filesystem or S3/MinIO) backends.
- A live SPA for DAG progress, outputs and lineage.

**Non-Goals (for this change):**
- Garbage collection of unreferenced blobs, and retention policies.
- Multi-tenant auth on the UI/API.
- A distributed lock service beyond DB-row leases.
- MLflow and lakeFS integration.
- A schema migration framework (Alembic). Tables are created with `create_all`.

## Decisions

### D1. Lineage DB is independent of Prefect's DB
Prefect runs tasks, and dendrite decides which tasks need to run. Prefect's own DB holds run state. Ours holds identity, provenance and the cache index.
- Prefect flow-run and task-run ids are stored as soft references.
- *Alternative:* Prefect artifacts and assets as the source of truth. Rejected, because they are not content-addressed and their lifecycle is tied to Prefect's API.

### D2. Execution signature
`signature = sha256(canonical_json({op, version, code, env, inputs, params, outputs}))`
- `inputs` maps each artifact-typed argument to its **content hash**. Nested lists and dicts of refs are allowed.
- `params` are only the non-artifact arguments of the node function, resolved with this precedence: experiment params `params[node][arg]`, then the value at the call site, then the function default.
- `code` is the sha256 of `inspect.getsource(fn)`. Nodes can opt out with `hash_source=False` and rely on `version` alone.
- `env` is the Python major.minor, `DENDRITE_ENV` (for example an OCI image digest), and the versions of declared `packages`.
- Non-JSON-serialisable params raise an error instead of silently hashing `repr`.

### D3. Cacheability is declared
`@artifact_task(cache=False)` nodes always execute. Use it for GPU-nondeterministic work where no reproducibility promise is wanted. Lineage is still recorded.

### D4. Leases in the metadata DB
A `leases(signature PK, owner, expires_at)` row acts as the lock. Acquiring it is an INSERT. On a unique-constraint violation, the caller checks expiry, steals the lease if it has expired, and otherwise polls the cache.
- A heartbeat thread extends the lease while the node runs.
- The cache is re-checked after acquiring the lease (double-checked locking).
- This works identically on SQLite and Postgres.
- *Alternative:* Prefect's `SERIALIZABLE` isolation with a lock manager. Rejected, because it is tied to Prefect's result-storage cache, which we bypass with `NO_CACHE`.

### D5. Content-addressed store
- Blobs live at `objects/<h[:2]>/<h[2:]>`.
- A directory is a manifest blob (`[[relpath, hash, size], …]`, canonical JSON). The directory hash is the manifest hash.
- Python values are pickled into a blob (kind `value`).
- `ArtifactStore` has two implementations:
  - `LocalStore`: atomic `os.replace`, hardlinked checkouts.
  - `FsspecStore`: any fsspec URL (`s3://`, `memory://`, `gs://`). It keeps a local materialisation cache.
- *Alternative:* storing whole directories as tarballs. Rejected, because a manifest lets identical files de-duplicate.

### D6. SQLAlchemy Core, not ORM
- Tables are defined once in a `MetaData`.
- The engine URL is either `sqlite:///…` (WAL mode, `busy_timeout`) or `postgresql+psycopg://…`.
- Short transactions only. Every publish (execution status, outputs, input edges, metrics, node run, lease release) commits in one transaction.

### D7. Only references cross Prefect boundaries
- Tasks receive and return `ArtifactRef(hash, kind, size, filename)` and nothing else. Prefect never serialises point clouds.
- Inside the node, inputs are materialised to local paths: read-only checkouts, or unpickled values.
- Prefect tasks use `cache_policy=NO_CACHE` so that the two caching layers never conflict.

### D8. Dynamic DAG, recorded at call time
- Pipelines are ordinary Prefect flows. When an `@artifact_task` is called, it writes a `queued` node run. When it starts, it writes `running`. Edges come from input hashes.
- This keeps Prefect's dynamic-DAG ergonomics (loops, conditionals, `.submit()`), and the UI sees nodes appear live.
- *Alternative:* a static DVC-style `dvc.yaml` DAG. Rejected as un-Prefect-like. The pipeline function is still the declarative source.

### D9. Experiment identity
- `id = "e-" + 10 hex` (random).
- Params, inputs and pins are frozen at creation.
- Re-running an experiment replaces its node runs but never mutates its configuration.
- A fork records `parent_id`, which gives a fork tree.

### D10. UI is a build-free SPA
- FastAPI serves a JSON API (`/api/experiments`, `/api/experiments/{id}/dag`, `/api/artifacts/{hash}/lineage`, and more) plus one static `index.html`.
- The page uses vanilla JS with its own layered DAG layout in SVG. It has no bundler or npm step and no CDN dependency.
- Progress is live through polling the DAG endpoint every 1s while an experiment is running.
- *Alternative:* React + Vite. Rejected for now because it adds a Node toolchain to a Python package. SSE was also rejected: polling is simpler and works behind any proxy.

## Risks / Trade-offs

- [Source hashing is sensitive to comments and whitespace] → `hash_source=False` plus an explicit `version`.
- [Pickled value artifacts are Python-version-coupled] → Python major.minor is part of `env`. Prefer file artifacts for long-lived outputs.
- [SQLite under heavy concurrency] → WAL plus busy timeout. Postgres is recommended for multi-worker deployments.
- [Lease holder crashes] → the TTL expires and another worker steals the lease. The heartbeat keeps long GPU jobs alive.
- [Blob deleted out-of-band] → a cache hit verifies blob existence first. A missing blob degrades to a recompute.
- [Identical content from different executions] → artifacts are keyed by hash, so ancestry may report several producers. This is correct for content addressing.

- [In-process execution only] tasks find their experiment, lab and DB through an in-process run registry. Prefect deployments, remote workers, and process/Dask/Ray task runners are not supported yet. → Phase 4: pass lab config (DB/store URLs) plus experiment id as task parameters and rebuild `Lab` in the worker.
- [Code fingerprint covers only the node function's own source] edits to helpers it calls do not invalidate the cache. → Bump `version`; a later change could hash the module or the declared `deps`.
- [Value artifacts are pickles] `load()` and the UI preview unpickle them. Anyone who can write to a shared bucket can therefore execute code on readers. → Only point a lab at stores you trust, keep `dendrite ui` on localhost, and prefer file artifacts for shared outputs.

## Migration Plan

Greenfield. Tables are created on first connect. Rollback means deleting `DENDRITE_HOME`.

## Open Questions

- Should Prefect `Asset` materialisation events be emitted per artifact so that Prefect Cloud lineage also shows them? This is deferred to a later change.
- Do we need a GC policy that keeps blobs referenced by any experiment younger than N days?
