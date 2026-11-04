<p align="center">
  <img src="docs/banner.webp" alt="dendrite" width="320">
</p>

<p align="center"><b>Experiment lineage and global caching for expensive pipelines, on top of <a href="https://github.com/PrefectHQ/prefect">Prefect 3</a>.</b></p>

You write a pipeline as ordinary Python functions. dendrite runs it through Prefect and:

- **Never computes the same thing twice.** Every step execution gets a signature from its code, inputs and the params it uses. If any experiment has already run that signature, its outputs are reused.
- **Makes forking cheap.** Fork an experiment, change one parameter, and only the steps that depend on it run again. You can also *pin* a step to an earlier result instead of recomputing it.
- **Records where every artifact came from**, across experiments: which step made it, from what inputs, and what used it.
- **Shows it all live** in a web UI: the pipeline graph, progress, lineage, diffs, drafts you can edit before running, and storage management.

It's built for work like 3D reconstruction, Gaussian splatting and ML training, where steps take minutes to hours and you mostly try one change at a time.

## Quickstart

```bash
uv sync
uv run python examples/minimal.py    # two steps, a run and a fork
uv run dendrite ui                    # http://127.0.0.1:8454
```

`examples/minimal.py` is all it takes:

```python
from pathlib import Path
from dendrite import Lab, artifact_task, pipeline

@artifact_task
def count_words(text: Path) -> dict:            # an input artifact arrives as a local Path
    words = text.read_text().split()
    return {w: words.count(w) for w in words}   # any picklable value becomes an artifact

@artifact_task
def top(counts: dict, n: int = 3) -> list:      # `n` has a default, so it's a parameter
    return sorted(counts, key=lambda w: (-counts[w], w))[:n]

@pipeline
def word_stats(text):
    return top(count_words(text))

lab = Lab()                                      # .dendrite/: SQLite metadata + a local store
base = lab.create(word_stats, inputs={"text": "speech.txt"}).run()
more = base.fork({"top.n": 5}).run()             # count_words is reused; only top runs
more.load("top")
```

## A realistic pipeline

```python
from pathlib import Path
from typing import NamedTuple
import dendrite
from dendrite import artifact_task, load, log_metric, pipeline, progress, stage, workdir

class Sfm(NamedTuple):                 # names the step's outputs
    cameras: Path
    sparse: Path

@artifact_task
def extract_frames(video: Path, fps: int = 2) -> Path:
    frames = workdir() / "frames"      # scratch space for this execution
    frames.mkdir()
    ...
    return frames                      # a Path becomes a file or directory artifact

@artifact_task
def colmap(frames: Path, matcher: str = "sequential") -> Sfm:
    ...
    return Sfm(cameras_json, sparse_dir)

@artifact_task
def train_splats(frames: Path, cameras: Path, sparse: Path, iterations: int = 30_000, seed: int = 42) -> Path:
    for step in range(iterations):
        ...
        progress(step + 1, iterations, "iterations")   # live on the step's card in the UI
    log_metric("psnr", 31.2)
    return workdir() / "splats.ply"

@pipeline
def reconstruction(video):
    with stage("Frames"):
        frames = extract_frames(video)
    with stage("Reconstruction"):
        cameras, sparse = colmap(frames)
        return train_splats(frames, cameras, sparse)

lab = dendrite.Lab()
a = lab.create(reconstruction, inputs={"video": "scan.mp4"}, params={"colmap.matcher": "exhaustive"}).run()
b = a.fork({"train_splats.iterations": 60_000}).run()        # frames and COLMAP reused
c = b.fork({"colmap.matcher": "sequential"}, pin={"colmap": a}).run()   # COLMAP frozen to a's result

a.diff(b)                               # params, shared executions, metric deltas
lab.lineage(b.output("train_splats"))   # where the model came from, and what used it
```

`examples/recon.py` is a runnable pipeline in this style.

## How it works

### Steps and parameters

An argument that receives an artifact is an **input**. Inside the step it arrives as a local `Path`, or as the original object for value artifacts. Every other argument is a **parameter**, resolved in this order:

1. the experiment's params (`"colmap.matcher"`)
2. the value passed at the call site
3. the function's default

A step's **signature** hashes its code, the content of its inputs, the params it uses and its environment. Experiment ids are never part of it, so forks and brand-new experiments share work. Changing `train_splats.iterations` never invalidates `colmap`.

Other details:

- Outputs are named by a `NamedTuple` or `TypedDict` return type, or by `outputs=[...]`. A single output needs no name.
- `colmap.override(matcher="exhaustive")` builds checked params for `fork()`: a typo raises with a suggestion.
- A cache hit is only used after its output files are verified to exist, so a deleted blob means a recompute, never a wrong result.
- Concurrent experiments compute a shared step once. The others wait for it, and a crashed worker's claim expires.

### The step contract

**The pipeline function only wires steps together.** It may branch on params, but it does no real work. It runs on every run, and whenever the UI previews a draft.

**A step can do anything** (GPU, network, files), but its outputs must depend only on what its signature hashes. Anything else is a *hidden input* that the cache can't see:

| Hidden input | Fix |
|---|---|
| Reading a file that wasn't passed in | Pass it as an artifact: `lab.ingest(path)`, or an experiment input |
| Environment variables, the clock, the hostname | Make them params, or put them in `DENDRITE_ENV` |
| Unseeded randomness | Add a `seed` param |
| A changed helper function | Bump `version=`: only the step's own source is fingerprinted |
| Inherently fresh data, like "latest" from an API | `@artifact_task(cache=False)` |

Params must be JSON values and are hashed exactly, so `30000` and `30000.0` give different signatures. Put anything large in an artifact instead.

Two safety nets catch mistakes:

- **Static checks** parse steps and pipelines (never running them) and warn about:

  - returns that don't match the declared outputs
  - unknown arguments
  - a step called twice
  - likely hidden inputs

  `dendrite check` exits 1 on errors, for CI.

- **A read guard** warns when a step reads a project file it didn't declare. Set `DENDRITE_CHECK_READS` to `warn` (the default), `error` or `off`.

### Fan-out, stages and what the pipeline reads

Call a step once per item with `step.named(key)`. Copies share the step's identity, so renumbered items still reuse their work. `dendrite.load` reads a result inside the pipeline function, for example a plan that decides the fan-out:

```python
@pipeline
def clips(video, defects):
    plan = load(plan_segments(defects))                 # the UI draws "decides" edges from it
    with stage("Privacy", "Sanitised media", color="teal"):
        return [blur.named(s).submit(extract.named(s).submit(video, **w)) for s, w in plan.items()]
```

- **Stages** group steps into the phases you think in. `stage` works as a `with` block or as a decorator on a function that calls steps, so stages nest and are reusable.
- **Display only:** stages, progress and reads never change a signature.
- **The UI's default view** is one card per stage. A fan-out is one card with a status grid however many items it has, and expanding it draws at most 12 items.

## The UI

`dendrite ui` serves the app. Pass `--import my_project.pipelines` so it can preview and edit drafts.

- **Pipeline graph:**

  - live status and progress, pushed as runs happen, even from another process
  - stages and fan-outs that fold into single cards
  - "reused from" links to the experiment that computed a result

- **Lineage:** trace any artifact back to its inputs and forward to what used it.

- **Drafts:** "Fork…" makes an editable copy. Change params or freeze steps, and every step shows whether it would be reused or run, with a reason and a time estimate. Running it freezes its settings: `dendrite run <exp> --import …`.

- **Code:** view each run's step source, edit the pipeline and watch its shape redraw as you type. You can save back to the file, on localhost only, with a conflict check.

- **Diff:** parameters, which steps were shared, and metric deltas between experiments.

- **Storage:**

  - what each experiment holds and what deleting it frees
  - delete experiments, keeping their forks if you like, or evict cache
  - clean up what nothing uses

  Every deletion previews first. Lineage records are always kept, and anything deleted recomputes when needed.

- **Files:** preview documents, images, media, archives and 3D models with [File Viewer](https://github.com/flyfish-dev/file-viewer). With a remote store, files over 8 MB load only when you ask, and downloads and video come straight from S3. File Viewer's CAD runtime is AGPL-3.0, and the PowerPoint runtime has its own license; see the [upstream licenses](https://github.com/flyfish-dev/file-viewer#license).

The built UI ships inside the wheel, so using dendrite never needs Node.js.

## CLI

| Command | |
|---|---|
| `dendrite list` · `show EXP` · `tree` | experiments and the fork tree |
| `dendrite diff A B` · `dag EXP` · `lineage HASH` | compare runs; Mermaid graphs |
| `dendrite run EXP --import MOD` | run a draft made in the UI |
| `dendrite check --import MOD` | static checks; exits 1 on errors |
| `dendrite ui [--port 8454] [--import MOD]` | the web UI |
| `dendrite du` · `rm EXP` · `keep EXP` · `evict EXEC` · `gc` · `clean` | storage; deletions preview unless `--yes` |

Each run also publishes a Prefect markdown artifact, `dendrite-<exp id>`, so lineage shows in the Prefect UI too.

## Sharing results with a team

Run a dendrite server, and everyone in a project shares one cache: what one person computed, the next person reuses. The server owns Postgres and the bucket. People sign in through GitHub or any OpenID Connect provider, and nobody else needs database or bucket credentials.

### Getting started on your laptop

Your pipelines still run on your machine. The server holds the shared history and files.

1. **Install dendrite** in your project (from git for now):

   ```bash
   uv add "dendrite @ git+https://github.com/soraxas/dendrite"
   ```

2. **Point the repo at your team's server**, once per repo, and commit the file it writes:

   ```bash
   dendrite init --server https://lineage.example.com --project recon
   git add dendrite.toml && git commit -m "Record runs to the team's dendrite server"
   ```

   `dendrite.toml` holds only the server and project, never credentials. Teammates who clone the repo get it too.

3. **Sign in**, once per laptop:

   ```bash
   dendrite login      # opens the browser; approve the code it shows
   dendrite whoami     # your projects and your role in each
   ```

   If you skip this, your first run asks you to sign in.

4. **Run your pipeline as usual:**

   ```bash
   python run.py
   ```

   Nothing in the code changes: `Lab()` finds `dendrite.toml`. Then:

   - **Steps anyone in the project already computed are skipped.** Their outputs download straight from the bucket, and only when a later step reads them.
   - **New outputs are uploaded** straight to the bucket, and only files the project doesn't already have.
   - **The run is recorded under your name**, with its parameters and lineage.

5. **Look at it with your team** at `https://lineage.example.com/p/recon/`:

   - **See who ran what**, live while it runs.
   - **Fork anyone's experiment** to change a parameter; the fork reuses their results.
   - **Compare runs.**

Your role in a project decides what you can do. Viewers browse; runners also run, edit drafts and delete their own experiments; admins also manage members and reclaim storage. An admin adds you with `dendrite member add recon you@example.com runner`.

**Without a repo**, set `DENDRITE_SERVER=https://lineage.example.com` and `DENDRITE_PROJECT=recon`, or pass `Lab(server=…, project=…)`. **In CI**, use a scoped token instead of signing in:

```bash
dendrite token create --project recon --role runner --name ci   # then set DENDRITE_TOKEN in CI
```

### Running the server

The server is `dendrite server run --config server.toml`, or the `Dockerfile` image: a FastAPI app plus Postgres and a bucket.

- [`examples/team`](examples/team) runs one on your machine with Docker, end to end.
- [`docs/deploying.md`](docs/deploying.md) covers production: sign-in apps, the bucket policy, replicas and `dendrite server check`.

## Configuration

| Variable | Default | |
|---|---|---|
| `DENDRITE_HOME` | `.dendrite` | local root: SQLite database, checkout cache, scratch |
| `DENDRITE_DB_URL` | SQLite in `$DENDRITE_HOME` | any SQLAlchemy URL, e.g. `postgresql+psycopg://…` (`uv sync --extra postgres`) |
| `DENDRITE_STORE_URL` | `$DENDRITE_HOME/store` | a directory, or `s3://bucket/prefix` and other fsspec URLs (`uv sync --extra s3`) |
| `DENDRITE_ENV` | — | an environment fingerprint mixed into every signature, e.g. an image digest |
| `DENDRITE_CHECK_READS` | `warn` | the undeclared-read guard: `warn`, `error` or `off` |
| `DENDRITE_GC_GRACE` | `3600` | seconds during which clean-up skips freshly written objects |
| `DENDRITE_SERVER`, `DENDRITE_PROJECT` | — | record to a dendrite server instead (also read from `dendrite.toml`) |
| `DENDRITE_TOKEN` | — | a server token for CI; people use `dendrite login` instead |
| `DENDRITE_PRESIGN` | `1` | downloads from S3 redirect to a short-lived signed URL; set `0` when browsers can't reach the store |

For MinIO, or any S3-compatible store, pass storage options:

```python
Lab(store_url="s3://lineage/prod", storage_options={"endpoint_url": "http://minio:9000", "key": "…", "secret": "…"})
```

## Limitations

- **Single process.** Runs use Prefect's default thread task runner. Deployments, remote workers, and process, Dask or Ray task runners aren't supported yet.
- **Narrow code fingerprint.** Only a step's own source is hashed; bump `version=` when its helpers change.
- **Value artifacts are pickles.** Only use stores you trust, and keep `dendrite ui` on localhost.
- **Prefect comes from `../prefect`** (`[tool.uv.sources]`). Delete that entry to use PyPI.

## Development

```bash
uv run pytest                          # Python tests (SQLite, local/memory/S3 stores, API)
DENDRITE_TEST_DB=postgres uv run pytest   # on Postgres, in a throwaway container (needs Docker)
cd ui && npm install && npm run dev    # hot-reloading UI; /api proxied to :8454
cd ui && npm test                      # UI tests (Vitest)
scripts/build-ui.sh && DENDRITE_REQUIRE_UI=1 uv build   # release: fails without the UI bundle
prek install                           # lint/format hooks (ruff, Biome, typos, …) on commit and push
prek run --all-files                   # run them all now
```

Design and requirements live in `openspec/`. CI runs only what a change touches. `python.yml` runs the tests on SQLite and Postgres, and `ui.yml` runs the UI tests and build. Docs-only changes run neither. `lint.yml` runs the prek hooks on every push. `CLAUDE.md` describes the architecture in detail.
