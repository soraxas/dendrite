## 1. Storage schema and metadata

- [ ] 1.1 Add `execution_logs`, `execution_samples`, `environments` and `execution_environments` tables to `db.py`
- [ ] 1.2 `Metadata.append_logs(exec_id, chunks)`, `set_log_tail(exec_id, text)`, `logs(exec_id, after, limit)`
- [ ] 1.3 `Metadata.upsert_metrics(exec_id, rows)`, and make `publish` tolerate metrics already written
- [ ] 1.4 `Metadata.record_sample(...)` with the history cap, and `samples(exec_id)`
- [ ] 1.5 `Metadata.record_environment(exec_id, snapshot)` (deduplicated by hash), `environment(exec_id)`
- [ ] 1.6 `scalars(exec_id)` and `experiment_scalars(exp_id)`; include log activity in `change_tokens`

## 2. Runtime capture

- [ ] 2.1 `_Tee` stdout/stderr proxy and root `logging` handler routed by `_exec`, installed once, delegating attributes, re-entrancy safe
- [ ] 2.2 `ExecContext` log buffer with throttled flush, head-plus-tail truncation (`DENDRITE_LOG_MAX`) and pending metrics
- [ ] 2.3 Live `log_metric` (throttled flush), with `title/series` handled at read time
- [ ] 2.4 `log_image` (path, PIL, numpy with PNG fallback encoder) and `log_media`
- [ ] 2.5 `dendrite.run(cmd)` streaming a subprocess into the log
- [ ] 2.6 Environment snapshot (git, diff, system, GPU, packages, `uv.lock` hash) with timeouts and memoisation
- [ ] 2.7 Wire into `task._compute` and the failure path: start capture, record the environment, write the traceback, final flush before publish or fail; export `log_image`, `log_media`, `run` from `dendrite`

## 3. Storage reclamation

- [ ] 3.1 Sample hashes of executions referenced by existing experiments are roots in `storage.py`

## 4. Server and CLI

- [ ] 4.1 `GET /api/executions/{id}/logs|scalars|samples|environment`, `GET /api/experiments/{id}/scalars`
- [ ] 4.2 `dendrite logs <exp> <node> [-f]` and `dendrite env <exp> <node>`

## 5. Hosted mode

- [ ] 5.1 `HttpMetadata` batches logs, tail and metrics on the flusher; telemetry methods in `wire.REMOTE`
- [ ] 5.2 `ServerMetadata` checks execution ownership for telemetry writes and committed blobs for samples

## 6. Tests

- [ ] 6.1 Capture: print, logging and stderr captured and still teed; concurrent steps separate; traceback on failure; live before finish
- [ ] 6.2 Truncation, the metrics-on-failure rule, the samples history cap, environment credential stripping and non-git directories
- [ ] 6.3 Cache hit shows original telemetry; plan writes nothing; signatures unchanged
- [ ] 6.4 API, CLI and hosted ownership tests

## 7. UI

- [ ] 7.1 `api.ts`/`queries.ts` telemetry queries with live polling while running
- [ ] 7.2 `Console.tsx` (ANSI, carriage-return collapse, follow, search, download)
- [ ] 7.3 `Scalars.tsx` with uPlot and smoothing; Metrics page charts with table toggle
- [ ] 7.4 `Samples.tsx` with step slider; `Environment.tsx`
- [ ] 7.5 Tabs in the node detail panel with the "from experiment X" note for reused executions
- [ ] 7.6 Vitest coverage for ANSI/carriage-return parsing, smoothing and the tabs

## 8. Docs

- [ ] 8.1 README section on telemetry; CLAUDE.md architecture notes; `examples/recon.py` uses `log_metric`, `log_image` and `dendrite.run`
