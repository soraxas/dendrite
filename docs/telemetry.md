# Watching runs: logs, charts, samples and environment

Every step's run is recorded centrally: what it printed, the metrics it logged, intermediate images it saved, and where it ran. You see it live in the UI, from the CLI, or from code, and none of it ever affects caching.

```python
import dendrite
from dendrite import artifact_task, log_image, log_metric

@artifact_task
def train_splats(frames: Path, iterations: int = 30_000) -> Path:
    dendrite.run(["colmap", "mapper", "--image_path", str(frames)])   # the tool's output goes into the log
    for step in range(iterations):
        loss, val = ...
        log_metric("loss/train", loss, step=step)      # one chart "loss", with a "train" line…
        log_metric("loss/val", val, step=step)         # …and a "val" line
        if step % 1000 == 0:
            print(f"step {step}: loss {loss:.4f}")     # print and logging are captured
            log_image("render", render(), step=step)   # a path, PIL image or numpy array
    ...
```

## What gets recorded

### Console

While a step runs, everything it writes to `sys.stdout`, `sys.stderr` or the `logging` module is copied into its log. It still appears in your terminal as usual.

- **Compiled code, subprocesses and threads.** While a step is the only one running, which is the usual case for a sequential pipeline, dendrite also captures file descriptors 1 and 2. Output from C/C++ extensions (pycolmap, CUDA libraries), from plain `subprocess.run` and `os.system`, and from threads the step starts lands in its log too.
- **Steps running in parallel.** When `.submit()` runs several steps at once, fd output can't be told apart, so only Python-level output is captured. Run subprocesses through `dendrite.run` to keep their output.
- **`dendrite.run(cmd, ...)`** It streams the child's combined stdout and stderr into the log and the terminal, returns a `CompletedProcess` whose `stdout` holds the output, and raises `CalledProcessError` on a non-zero exit unless `check=False`. Outside a step it behaves like `subprocess.run` with output shown live.
- **Failures.** A step that raises ends its log with the traceback.
- **Concurrent steps** (`.submit()`) keep separate logs.
- **Size limit.** A log keeps at most `DENDRITE_LOG_MAX` bytes (16 MiB by default). Past that it keeps the beginning and the most recent output, with a marker saying how much was dropped in between.
- **Big logs live in the store.** While a step runs, its log streams through the database so you can follow it. When the step ends, a log bigger than `DENDRITE_LOG_INLINE` (1 MiB) is written to the artifact store as one file. It's cleaned up with its experiment like any other blob. The console shows its last 256 KB with a link to the whole file, and `dendrite logs` prints all of it.
- **Progress bars.** tqdm-style bars that redraw with `\r` show as one line in the UI.

What isn't captured:

- **While steps run in parallel:** fd-level output (compiled code, plain `subprocess`) and output from threads a step starts
- **In a notebook kernel, on Windows, or with `DENDRITE_CAPTURE_FD=0`:** fd-level output, since only Python-level capture is used there

Common console libraries work as usual: `rich`, `click`, colour codes (the UI renders them), and a step's own `contextlib.redirect_stdout`, which keeps its output out of the log.

### Scalars

`log_metric(key, value, step=0)` is written while the step runs, so charts update live, and the metrics of a failed run are kept.

- **Times.** Each point records when it was logged, so charts can plot against elapsed time as well as step.
- **Grouping.** A key with a `/` is split at the first `/` into a chart title and a series: `loss/train` and `loss/val` are two lines on one `loss` chart. A key without `/` is its own chart.
- **Repeats.** Logging the same key and step twice keeps the last value.

### Samples

`log_image(title, image, step=0, series=None)` saves an intermediate image, and `log_media(title, path, step=0, series=None)` saves any other file (video, audio, a point cloud…).

- **Accepted images:** a file path (stored as is), a PIL image, or a numpy array (H×W, H×W×3 or H×W×4; `uint8`, or floats in `[0, 1]`), which is stored as PNG.
- **History.** The latest `DENDRITE_SAMPLE_HISTORY` steps (100 by default) are kept per title and series; older ones are dropped as new ones arrive.
- **Not outputs.** Samples live in the artifact store, but they aren't step outputs and never enter a signature.
- **Kept with their experiments.** Clean-up keeps a sample while any experiment that ran or reuses its step exists.

### Environment

When a step starts, dendrite records where it runs:

- the git commit, branch, remote (with any credentials removed) and whether the tree was dirty, plus the uncommitted diff (up to 1 MiB; `DENDRITE_CAPTURE_DIFF=0` leaves it out)
- the Python version and executable, platform, host name, and GPUs (when `nvidia-smi` is available)
- installed packages, the hash of `uv.lock`, and `DENDRITE_ENV`

Environment variables are never recorded. Identical snapshots are stored once.

## Reused results bring their telemetry

Telemetry belongs to the run that did the work. When a fork reuses `colmap` from its parent, the fork's `colmap` shows the parent run's console, charts and samples, labelled "From the run in *parent*". You never lose the logs of a result just because it came from the cache.

## Viewing it

### In the UI

Select a step. Its panel has a **Run** section with four tabs:

- **Console:** follows the output live; filter lines, toggle follow, or download the whole log. Only the last 2,000 lines are drawn unless you filter or ask for all.
- **Scalars:** one chart per title, with a line per series and the values under the pointer. The controls apply to every chart: smoothing, **Step** or **Time** on the x-axis, **log scale** (offered when every value is positive), and zoom. Drag across a chart to zoom in, and double-click or press **Reset zoom** to zoom back out.
- **Samples:** each title with a slider over its logged steps, starting at the latest.
- **Environment:** git state and diff, Python, system, GPUs and packages.

The experiment's **Metrics** page charts every step at once. **Overlay** adds the parent, forks or any other experiment to the same charts, with each line labelled by experiment. The **Table** toggle shows the last value of each metric.

### From the CLI

```bash
dendrite logs EXP NODE        # print a step's console
dendrite logs EXP NODE -f     # follow a running step until it finishes
dendrite env EXP NODE         # where it ran; --json for everything, including packages
```

### Over HTTP

| Endpoint | Returns |
|---|---|
| `GET /api/executions/{id}/logs?after=SEQ&limit=N` | log chunks after a sequence number, whether the run is still going, and `stored` (hash and size) when the log was moved to the store |
| `GET /api/executions/{id}/scalars` | every metric point, with `t` when it was logged |
| `GET /api/executions/{id}/samples` | logged samples, each downloadable at `/api/artifacts/{hash}/download` |
| `GET /api/executions/{id}/environment` | the environment snapshot |
| `GET /api/experiments/{id}/scalars` | the metric points of every step in an experiment |

## On a team server

When you record to a dendrite server, a step's console and metrics are sent in batches every couple of seconds, and samples upload through the same path as outputs. Everyone in the project sees them. Only the person who ran a step can write to its telemetry.

## Settings

| Variable | Default | |
|---|---|---|
| `DENDRITE_LOG_MAX` | `16777216` | bytes of console output kept per run (the beginning and the end) |
| `DENDRITE_LOG_INLINE` | `1048576` | finished logs bigger than this move from the database to the artifact store |
| `DENDRITE_CAPTURE_FD` | `1` | `0` captures Python-level output only (no file descriptor capture) |
| `DENDRITE_SAMPLE_HISTORY` | `100` | logged steps kept per sample title and series |
| `DENDRITE_CAPTURE_DIFF` | `1` | `0` leaves the uncommitted git diff out of the environment |
