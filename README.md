# dendrite

dendrite adds artifact-first experiment lineage, global caching and forking on top of **Prefect 3**.

Prefect runs the DAG, and dendrite decides which nodes actually need to run. Every node execution gets a deterministic **execution signature**, built from:

- the operation
- its version
- its code
- its environment
- the content hashes of its inputs
- the params it consumes

Any experiment that resolves to a signature that has already been seen reuses that execution's outputs. It doesn't matter which experiment produced them. Experiments are immutable snapshots, and they can be forked by changing params or by pinning intermediate artifacts.

```
Experiment API (create · fork · pin · diff)     dendrite ui (live DAG, lineage, diff)
                 │                                         │
   @pipeline ── Prefect flow ── @artifact_task ── Prefect task (NO_CACHE)
                 │
        Lineage engine: signature → cache lookup → lease → execute → atomic publish
          │                                             │
   SQLAlchemy metadata (SQLite | Postgres)     Content-addressed store (filesystem | S3/MinIO/GCS via fsspec)
```

## Quickstart

```bash
uv sync                                   # prefect comes from ../prefect (editable)
uv run python examples/recon.py           # baseline + config fork + artifact fork
uv run dendrite ui                        # http://127.0.0.1:8454 (read-only; no imports needed)
```

```python
from pathlib import Path
import dendrite
from dendrite import artifact_task, pipeline, workdir, log_metric

@artifact_task(outputs=["frames"])
def extract_frames(video: Path, fps: int = 2) -> Path:
    out = workdir() / "frames"; out.mkdir()
    ...
    return out                               # Path -> file/dir artifact, anything else -> pickled value

@artifact_task(outputs=["cameras", "sparse"], version="2")
def colmap(frames: Path, matcher: str = "sequential"):
    ...
    return cameras_json, sparse_dir

@artifact_task(outputs=["model"], packages=["torch"])
def train_splats(frames: Path, cameras: Path, sparse: Path, iterations: int = 30_000, seed: int = 42):
    log_metric("psnr", 31.2)
    ...

@pipeline(name="reconstruction")
def reconstruction(video):
    frames = extract_frames(video)
    cameras, sparse = colmap(frames)
    return train_splats(frames, cameras, sparse)

lab = dendrite.Lab()
a = lab.create(reconstruction, inputs={"video": "scan.mp4"},
               params={"colmap": {"matcher": "exhaustive"}}).run()

b = a.fork({"train_splats.iterations": 60_000}).run()     # extract + colmap: cached
c = b.fork({"colmap.matcher": "sequential"},              # artifact fork: freeze a's COLMAP
           pin={"colmap": a}).run()                       #   colmap: pinned, never executed
a.diff(b)            # param changes, shared executions, metric deltas
lab.lineage(b.output("train_splats"))   # ancestors / descendants / experiments using it
```

### How arguments are classified

Arguments that receive `ArtifactRef`s are **artifact inputs**. Refs may also be nested in lists or dicts. Inside the node they arrive as local `Path`s, or as the original object for value artifacts.

Every other argument is a **parameter**. A parameter's value is resolved in this order:

1. the experiment's `params[node][arg]`
2. the value passed at the call site
3. the function's default

Only the params a node consumes go into its signature. Changing `train_splats.iterations` never invalidates `colmap`. Unknown param keys are rejected.

### Caching semantics

- A signature never includes experiment or flow-run ids, so forks and brand-new experiments share work.
- A cache hit is only used after the output blobs are verified to exist, including every file of a directory. A deleted blob degrades to a recompute.
- Cache keys follow content, not lineage. If an upstream change produces byte-identical output, downstream nodes are still hits.
- **Leases.** A lease row per signature means concurrent experiments compute a node once, and the others wait and reuse the result. A heartbeat keeps long GPU jobs alive. A crashed holder's lease expires and gets stolen.
- `@artifact_task(cache=False)` marks non-deterministic nodes. They always execute, and lineage is still recorded.
- `hash_source=False` together with `version="…"` keeps cosmetic code edits from invalidating the cache.

## Configuration

| env var | default | |
|---|---|---|
| `DENDRITE_HOME` | `.dendrite` | local root (SQLite DB, materialisation cache, scratch) |
| `DENDRITE_DB_URL` | `sqlite:///$HOME/lineage.db` | any SQLAlchemy URL, e.g. `postgresql+psycopg://user:pw@host/db` (`uv sync --extra postgres`) |
| `DENDRITE_STORE_URL` | `$HOME/store` | a directory, or an fsspec URL such as `s3://bucket/prefix` (`uv sync --extra s3`) |
| `DENDRITE_ENV` | — | extra environment fingerprint (e.g. an OCI image digest) mixed into every signature |

For MinIO, pass storage options:

```python
Lab(store_url="s3://lineage/prod", storage_options={"endpoint_url": "http://minio:9000", "key": "…", "secret": "…"})
```

## UI and CLI

`dendrite ui` serves a JSON API (`/api/...`) and a React single-page app (React Flow, laid out by ELK). The app shows:

- an experiment fork tree, with per-experiment status bars
- a live DAG (queued → running/waiting → computed/cached/pinned/failed). Node status updates in place, and the graph is only re-laid-out when new nodes appear.
- node details: signature, the execution it reused and which experiment computed it, params, inputs/outputs, and metric sparklines
- artifact lineage: artifacts (data) and executions (computation) are drawn as distinct node types, and artifacts are labelled by output name. The selection is kept in the URL and the side panel always describes it. "Used by" reads like *produced by extract_frames · read by colmap, train_splats*.
- an experiment diff: params, per-node reuse, and metric deltas

The built UI ships inside the wheel, so installing and running dendrite never needs Node.js. If the bundle hasn't been built (for example in a fresh clone), `dendrite ui` falls back to the older single-file page and says so. `/api/info` reports which UI is active.

```bash
dendrite list | show EXP | diff A B | dag EXP | tree | lineage HASH [--mermaid] | ui
```

Each run also publishes a Prefect markdown artifact (`dendrite-<exp id>`) with the node table and a Mermaid DAG, so the lineage is visible in the Prefect UI too.

## Current limitations

- **Single process.** Prefect's default thread task runner works. Deployments, remote workers, and process, Dask or Ray task runners are not wired up yet (planned for Phase 4).
- **Code fingerprint is narrow.** It covers the node function's own source only. Bump `version=` when helpers it calls change.
- **Value artifacts are pickles,** and the UI or `load()` unpickles them. Only use stores you trust, and keep `dendrite ui` bound to localhost.
- **Prefect source dependency.** `prefect` resolves from `../prefect` via `[tool.uv.sources]`. Delete that entry to use PyPI instead.

## Development

The design and requirements live in OpenSpec: the baseline specs are in `openspec/specs/`, and changes in flight are in `openspec/changes/`. Use `/opsx:propose`, `/opsx:apply` and `/opsx:archive` for new changes.

```bash
uv run pytest                                   # SQLite + local/memory/moto-S3 stores, API, packaging
DENDRITE_TEST_PG_URL=postgresql+psycopg://postgres:pw@localhost:55432/dendrite uv run pytest tests/test_postgres.py
```

### Web UI

The UI source lives in `ui/` (React + TypeScript + Vite). Working on it needs Node.js 20+.

```bash
uv run dendrite ui                 # backend on :8454
cd ui && npm install && npm run dev   # hot-reloading UI; /api is proxied to :8454 (override with DENDRITE_API=…)
npm run build                      # type-check + bundle into src/dendrite/ui/dist (git-ignored)
```

To release, build the UI first and then the Python package:

```bash
scripts/build-ui.sh && uv build
```

`hatch_build.py` makes `uv build` fail if `src/dendrite/ui/dist/index.html` is missing. Editable installs (`uv sync`) are exempt. Set `DENDRITE_SKIP_UI_CHECK=1` to deliberately build a wheel that only has the legacy page.
