## Context

Steps run inside `ArtifactTask._compute` under an `ExecContext` held in the `_exec` contextvar. Prefect may run tasks on worker threads, and `.submit()` runs several steps at once in one process. `log_metric` appends to `ctx.metrics`, which `db.publish` writes in the publish transaction, so metrics appear only on success. `progress()` already writes live, throttled rows through `ctx.db`. In hosted mode `HttpMetadata.set_progress` batches them on a flusher thread. The schema has no migrations: new data needs new tables (`create_all`).

## Goals / Non-Goals

**Goals:** live console, scalars, samples and environment per execution, viewable centrally (locally or hosted), with negligible overhead and no effect on caching.

**Non-Goals:** file-descriptor-level capture, capture from threads a step starts, framework auto-logging, plots and tables, cross-experiment compare.

## Decisions

### 1. Route output by contextvar through a process-wide proxy

On first use, `runtime` replaces `sys.stdout`/`sys.stderr` with a `_Tee` proxy and adds one `logging.Handler` to the root logger. On every write, the proxy forwards to the original stream, then looks up `_exec.get()`. If the current thread runs a step, the text is appended to that context's log buffer. The step thread's context is set by `_compute`, so concurrent steps on different threads stay separate. Threads started by a step don't inherit the contextvar, so their output simply isn't captured, which is the documented limitation.

*Alternative:* `os.dup2` on fd 1/2. That would capture C code and subprocesses, but it's process-global, so concurrent steps would mix. It was rejected. Subprocesses, which matter most for recon tools, are handled by `dendrite.run` instead.

*Alternative:* `contextlib.redirect_stdout`. That is also process-global, and it breaks under concurrency.

The proxy delegates `fileno`, `isatty`, `encoding` and every other attribute to the original stream, so libraries that look at the terminal (tqdm, rich) behave the same. A re-entrancy guard stops the logging handler from looping if it writes to stderr.

### 2. One buffer per execution, flushed by the existing throttle pattern

`ExecContext` gains `log: _LogBuffer` (pending chunks, the byte count, the truncation state) and `pending_metrics`. A write triggers a flush only when `LOG_INTERVAL` (0.5 s) has passed since the last one. The flush runs on the writing thread, so a step that goes quiet still has its last output written at end-of-step.

Before `publish` or `fail_execution`, `_compute` and the failure path call `ctx.flush(final=True)`. The traceback is written to stderr before that final flush.

*Alternative:* a background flusher thread per execution. That costs more threads and makes ordering against publish harder. The `HttpMetadata` side already has a flusher for network batching.

### 3. Truncation: head plus a rolling tail

Once the stored bytes reach `max/2`, new chunks go into an in-memory tail ring of at most `max/2` bytes instead of the database. At the final flush, the marker (`… N bytes truncated …`) and the tail are written. While running, the tail is also flushed to a single `tail` row (`seq = -1`), which is overwritten. That way a live viewer still sees recent output of a huge log. Memory is bounded at `max/2` per execution.

### 4. Tables

- `execution_logs(execution_id, seq, stream, t, text)`, with primary key `(execution_id, seq)`. Chunks are concatenations of writes of up to about 64 KB.
- **Metrics:** the existing `metrics` table, written incrementally with upsert-by-delete-and-insert (portable), in the same throttled flush. `publish` writes only what's left. Charts use `step` as the x-axis; wall time is left out because `metrics` has no time column and we don't migrate.
- `execution_samples(execution_id, title, series, step, hash, kind, filename, size, t)`, with primary key `(execution_id, title, series, step)`. The history cap is applied when a sample is recorded: older steps of that title and series are deleted. Those rows are display-only, so nothing trusts them.
- `environments(hash PK, data JSON)` and `execution_environments(execution_id PK, env_hash)`.

### 5. Samples are artifacts in the store, not outputs

`log_image` encodes to PNG (with PIL if it's installed, and a tiny pure-numpy PNG encoder as the fallback). It writes a temp file in the workdir, calls `store.put_path` and `db.register_artifacts`, then records the sample row. Because they're real artifacts, download, presigning, previews and hosted upload all work unchanged. GC treats sample hashes of executions referenced by any existing experiment's node runs (or with `executions.experiment_id` of an existing experiment) as roots. Deleting the experiment makes them collectable. This never risks a wrong result, because nothing trusts a sample.

### 6. Environment snapshot once per process and project

Capture is memoised per `(project_root, DENDRITE_ENV)` for the process, except for the git dirty state and diff, which are memoised per run because they're cheap to recheck. Each command (`git rev-parse`, `git status --porcelain`, `git diff HEAD`, `nvidia-smi -L`) runs with a 2 s timeout. Packages come from `importlib.metadata.distributions()`, sorted. The snapshot is canonical JSON, hashed with SHA-256 and stored with insert-if-absent.

### 7. Hosted mode

`HttpMetadata` gains `append_logs`, `set_tail` and `upsert_metrics`, batched on the existing flusher alongside progress and flushed before any other call. `log_image` goes through `ServerStore`, which uploads and commits the blob before `record_sample`. `wire.REMOTE` lists the reads (`viewer`) and the writes (`runner`). `ServerMetadata` applies the same `_own_execution` check that `set_progress` uses (the execution's recorded owner must be the caller) to every telemetry write.

### 8. UI

- **Console:** polls `logs?after=` every second while the execution runs, then stops. Lines are built incrementally as chunks arrive (carriage returns applied once per finished line), and only the last 2,000 are rendered unless the user filters or asks for all. Memoised lines skip re-rendering.
- **ANSI:** a tiny SGR parser covering 16/256 colours, bold and reset.
- **Carriage returns:** text after the last `\r` on a line replaces the line.
- **Charts:** plain SVG with min/max downsampling to about 800 points per series, so there's no chart dependency. Smoothing is a debiased EMA applied in the UI.
- **Samples:** reuse `ArtifactPreview` for the selected step's blob, with thumbnails through the existing download endpoint.

## Risks / Trade-offs

- **Proxying `sys.stdout` changes a global.** → It's installed once, delegates every attribute, and is a no-op cost (one contextvar lookup) outside steps. Reassigning `sys.stdout` elsewhere bypasses capture, which is acceptable.
- **SQLite writer contention** with many parallel chatty steps. → The 0.5 s batching means writes per second are about 2 × the number of running steps.
- **Diffs or logs may contain secrets.** → Environment variables are never recorded, `DENDRITE_CAPTURE_DIFF=0` turns off diffs, and logs are as sensitive as the terminal output they mirror.
- **Large sample volumes.** → The history cap, plus GC once experiments are deleted.

## Migration Plan

New tables only, created on connect. Existing metrics keep working, and older executions just have no logs, samples or environment.

## Open Questions

- Should a wall-time x-axis come later through a separate `metric_times` table?
- Should pipeline-body output be captured into an experiment-level log?
