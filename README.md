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

### The step contract

dendrite splits work the way Temporal does, with a softer rule.

**The pipeline function only wires steps together.** It may branch on params, but it shouldn't do real work or have side effects. It runs on every run, and also whenever the UI plans a draft.

**A step can do anything** (GPU, network, writing files), but it must be *hermetic in what it declares*. Its outputs should depend only on what goes into its signature:

- its code
- its params
- its artifact inputs
- `DENDRITE_ENV` and the declared `packages`

Anything else is a **hidden input**, and the cache can't see it. A hidden input changing won't trigger a recompute, so stale results get reused. Common ones, and their fixes:

| Hidden input | Fix |
|---|---|
| Reading a file that wasn't passed in | Pass it as an artifact: `lab.ingest(path)`, or an experiment input |
| Environment variables, the clock, hostnames | Make them params, or put them in `DENDRITE_ENV` |
| Unseeded randomness | Add a `seed` param |
| A changed helper function | Bump `version=`. Only the step's own source is fingerprinted |
| Inherently fresh data, such as "latest" from an API | `@artifact_task(cache=False)` |

Params don't need type hints, but they must be JSON-serialisable (paths, sets, dataclasses and pydantic models are converted). Values are hashed exactly, so `30000`, `30000.0` and `"30000"` give different signatures. Anything large or non-JSON should be an artifact instead.

**Guard for undeclared reads.** dendrite warns when a step opens a file for reading under the project directory that isn't one of its declared inputs. Set `DENDRITE_CHECK_READS` to choose what happens:

- `warn`: the default
- `error`: fail the step
- `off`: no check

The check is best-effort: it sees Python-level opens only, not C extensions or subprocesses.

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
- code: every run records a snapshot of its step's source, with file and line. You can view it in an editor. While the file still contains exactly that code, you can also edit and save it back. Saving asks for confirmation, refuses if the file changed underneath you, and is only enabled when `dendrite ui` runs on localhost (or with `--allow-edit`).

### Drafts: change an experiment before it runs

"Fork…" on any experiment creates a **draft**, the one kind of experiment you can still change.

- **Editing.** In draft mode you change parameters on a step, or freeze a step to the parent's result. Edits save as you go.
- **Preview.** Every step shows what running the draft would do before anything runs:
  - **Reused**, and from which experiment
  - **Will run**, with the reason (its parameters, code or inputs changed, or an earlier step will run) and a time estimate from earlier runs
  - **Frozen**
- **Summary.** The toolbar adds it up, e.g. "Will run 2 of 4 steps · about 14 min".
- **Running.** Run the draft with the command shown in the bar. Running is what freezes its settings.

```bash
dendrite ui --import my_project.pipelines     # the server must load your pipeline code to preview drafts
dendrite run e-1234abcd --import my_project.pipelines
```

The same is available from Python:

```python
d = base.fork({"train_splats.lr": 2e-3}, draft=True)
d.update({"train_splats.iterations": 90_000}, reset=["train_splats.lr"])
d.plan()["totals"]   # {'steps': 4, 'run': 2, 'reuse': 2, 'frozen': 0, 'estimate': 1820.0, ...}
d.run()              # freezes the draft and runs it
```

`plan()` runs your pipeline function with steps that stop after the cache check. Predictions therefore use exactly the cache keys a real run would. A plan never executes a step or writes anything.

A plan has two limits:
- **It's an upper bound.** A step downstream of one that will run is shown as "will run". If the earlier step turns out to produce the same output, it is reused after all.
- **It can stop early.** If the pipeline function branches on the *contents* of a result that doesn't exist yet, the preview stops there and says so.

The built UI ships inside the wheel, so installing and running dendrite never needs Node.js. If the bundle hasn't been built (for example in a fresh clone), `dendrite ui` falls back to the older single-file page and says so. `/api/info` reports which UI is active.

```bash
dendrite list | show EXP | diff A B | dag EXP | tree | lineage HASH [--mermaid] | run EXP | ui
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
