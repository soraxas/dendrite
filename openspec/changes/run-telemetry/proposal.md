## Why

dendrite records what each step produced, but almost nothing about how the run went. Metrics are a table that appears only after a step finishes. Console output disappears into whichever terminal ran it. Nothing records the git state or package versions a result was made with. For long reconstruction and training runs, people want what ClearML-style trackers give them: live logs, live charts, intermediate renders and the environment, all in one central place. Because dendrite shares executions across experiments, a reused result can bring its original logs and charts along, which no per-task tracker can do.

## What Changes

- **Console capture.** While a step runs, its `print` output, `sys.stdout`/`sys.stderr` writes and `logging` records are teed (still shown in the terminal) into an execution log, in chunks.

  - Output from subprocesses (COLMAP, ffmpeg…) is captured by a new helper, `dendrite.run(cmd, ...)`, which streams the child's output into the log and raises on failure.
  - Carriage-return progress bars (tqdm) are kept as written, and the UI collapses them.
  - Logs have a size cap (`DENDRITE_LOG_MAX`, default 16 MB per execution). Past it, the head and the most recent tail are kept, with a marker in between.

- **Live scalars.** `log_metric` writes while the step runs, throttled like progress, instead of only at publish. Keys may use `title/series` (`loss/train`, `loss/val`): each title becomes a chart, each series a line. Metrics of failed executions are kept.

- **Debug samples.** `log_image(title, image, step=0, series=None)` and `log_media(title, path, step=0, series=None)` record intermediate images, videos, audio or 3D files.

  - Samples are stored as content-addressed blobs and registered as artifacts, but they are not step outputs and never enter a signature.
  - Only the last `DENDRITE_SAMPLE_HISTORY` steps (default 100) are kept per title and series.

- **Environment capture.** When an execution starts, dendrite records:

  - the git commit, branch, remote (credentials stripped) and dirty state, plus the uncommitted diff (capped; `DENDRITE_CAPTURE_DIFF=0` turns it off)
  - Python, platform, host and GPU (when `nvidia-smi` is present)
  - installed package versions, the hash of `uv.lock` when there is one, and `DENDRITE_ENV`

  Identical environments are stored once.

- **All of it is display-only.** Logs, scalars, samples and environment never enter a signature, plan or cache decision. They belong to the execution, so every experiment that reuses it shows them, labelled with the experiment that ran it.

- **UI.**

  - The node detail panel gains **Console** (live tail, follow, search, ANSI colours, download), **Scalars** (charts by title, smoothing), **Samples** (grid per title with a step slider) and **Environment** (git, diff, packages) tabs.
  - The experiment Metrics page becomes charts across the experiment's steps, with the table kept as a fallback.
  - Updates arrive live through the existing event stream.

- **API.**

  - `GET /api/executions/{id}/logs?after=&limit=`
  - `GET /api/executions/{id}/scalars`
  - `GET /api/executions/{id}/samples`
  - `GET /api/executions/{id}/environment`
  - `GET /api/experiments/{id}/scalars`

- **CLI.** `dendrite logs <exp> <node> [-f]` and `dendrite env <exp> <node>`.

- **Hosted mode.** Steps on a client batch logs and scalars to the server, like progress. Samples upload through the existing blob path before their rows are written. Only the caller holding an execution's lease may append to it.

- **Non-goals:**

  - capturing output written straight to file descriptor 1/2 by C code or by subprocesses not started through `dendrite.run`
  - logs from threads a step starts
  - pipeline-body output
  - automatic logging of TensorBoard, Lightning or other frameworks (a possible follow-up)
  - plots and tables (`log_table`, Plotly)
  - an experiments table and N-way compare, which are a separate change

## Capabilities

### New Capabilities

- `run-telemetry`: console capture, live scalars, debug samples and environment capture per execution, with their storage, limits, API, CLI and hosted behaviour.

### Modified Capabilities

- `lineage-ui`: the node detail panel gains the Console, Scalars, Samples and Environment tabs, and the Metrics page shows charts.

## Impact

- **`runtime.py`:** `ExecContext` gains a log buffer and a flusher. Adds `log_image`, `log_media` and `run`, and a process-wide stdout/stderr proxy plus a `logging` handler routed through the `_exec` contextvar.
- **`task.py`:** `_compute` starts and stops capture, records the environment, and flushes before publish.
- **`db.py`:** new tables `execution_logs`, `execution_samples`, `environments` and `execution_environments`. Metrics are written incrementally. `change_tokens` covers live logs. All of these are created by `create_all`, with no migration.
- **`storage.py`:** sample blobs are roots while their execution's experiment exists. Trimming old sample rows only removes display rows.
- **`server.py`, `cli.py`, `remote.py`, `hosted/wire.py`, `hosted/authz.py`:** the new endpoints, commands and remote methods.
- **UI:** new `components/Console.tsx`, `Scalars.tsx`, `Samples.tsx` and `Environment.tsx`; changes to `DetailPanel.tsx`, `Pages.tsx`, `api.ts` and `queries.ts`. Charts are plain SVG, so there's no new dependency.
