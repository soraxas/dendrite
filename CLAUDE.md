# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is for

dendrite is an artifact-first experiment lineage and caching layer on top of Prefect 3, in the spirit of DVC plus MLflow. It targets expensive pipelines such as 3D reconstruction, Gaussian splatting and ML training, where researchers fork an experiment, change one parameter, and want everything upstream reused.

Prefect executes the DAG, and dendrite decides which nodes actually need to run:

- Every node execution gets a deterministic **execution signature**.
- An execution whose signature has been seen before, in *any* experiment, reuses those outputs.
- Every artifact's provenance is recorded.
- Experiments are immutable snapshots. They can be forked in two ways:
  - by changing params (a config fork)
  - by pinning an intermediate artifact (an artifact fork)

## Commit and PR rules

- Never add coding-agent attribution to commits, PR descriptions or code. That means no `Co-Authored-By: Claude…` trailers and no "Generated with Claude Code" lines.

## Commands

```bash
uv sync                                    # Prefect from PyPI; for a local checkout: uv pip install -e ../prefect
uv run pytest                              # full suite (Prefect test harness + moto S3 server)
uv run pytest tests/test_lineage.py::test_artifact_fork_pins_node   # single test
uv run pytest -k pin                       # by keyword
DENDRITE_TEST_DB=postgres uv run pytest   # whole suite on Postgres (testcontainers; needs Docker)
DENDRITE_TEST_REMOTE=1 uv run pytest      # whole suite through an in-process dendrite server (Postgres + moto)
uv run dendrite server run --config examples/team/server.toml   # hosted server; see examples/team/README.md
uv run dendrite server check --config server.toml                # probe a deployment's database, bucket and sign-in
uv run dendrite push --all                 # copy a local lab's experiments to the configured server (push.py)
docker build -t dendrite-server .          # the server image (Prefect from PyPI); docs/deploying.md
uv run python examples/minimal.py          # smallest setup: two steps, a run and a fork
uv run python examples/recon.py            # demo: baseline + config fork + pinned fork (DEMO_SLEEP=2 to slow nodes)
uv run dendrite ui --port 8454 --import examples.recon   # API + SPA; --import enables draft previews
uv run dendrite run <exp> --import examples.recon        # run a draft made in the UI
uv run dendrite check --import examples.recon            # static checks; exit 1 on errors
uv run dendrite du                                       # storage inventory; rm | keep | evict | gc | clean preview unless --yes
                                           # also: list | show | diff | dag | tree | lineage
cd ui && npm run dev                       # hot-reloading UI, /api proxied to :8454 (Node 20+; UI work only)
cd ui && npm run build                     # tsc type-check + vite build into src/dendrite/ui/dist (git-ignored)
cd ui && npm test                          # Vitest + Testing Library (jsdom): UI logic and draft-editing components
scripts/build-ui.sh && DENDRITE_REQUIRE_UI=1 uv build   # release: fails rather than ship a wheel without the UI
```

Postgres for tests comes from the session fixture `pg_url` in `conftest.py`:

- **Source.** `DENDRITE_TEST_PG_URL` if set, otherwise a throwaway `postgres:17-alpine` container (testcontainers, random port).
- **Without Docker.** The tests skip, or fail with `DENDRITE_TEST_PG=require`, which CI sets.
- **Isolation.** `pg_schema_url` gives each test its own schema and drops it afterwards.
- **Whole suite.** `DENDRITE_TEST_DB=postgres` switches the shared `lab` fixture to Postgres. Tests that open a second `Lab` or call the CLI must pass `lab.settings.db_url` / `--db-url` along.

CI has two path-filtered test workflows, so a docs-only push runs no tests. `.github/workflows/python.yml` runs pytest on SQLite and on Postgres when `src/`, `tests/`, `examples/` or the packaging files change. `.github/workflows/ui.yml` runs the UI tests and build when `ui/` changes.

Linting and formatting run through [prek](https://github.com/j178/prek) in workspace mode, with two projects:

- **Root, `.pre-commit-config.yaml`.** File hygiene, `uv-lock`, ruff check and format (settings in `[tool.ruff]`), validate-pyproject, typos (report only; allowed words in `_typos.toml`), shellcheck, mdformat, zizmor and the GitHub workflow schema. actionlint is `manual`. Root hooks also see `ui/` files.
- **`ui/.pre-commit-config.yaml`.** Biome (`ui/biome.json`; the a11y rules that don't fit the hash-link UI are off) and `tsc` through `npm run typecheck`. Child hooks run inside `ui/` with paths relative to it.

`prek install` sets up the pre-commit and pre-push hooks. Run `prek run --all-files` to check everything, and `prek run actionlint --hook-stage manual` for the workflows. `.github/workflows/lint.yml` runs prek, including actionlint, on every push.

Configuration env vars:

- `DENDRITE_HOME` (default `.dendrite`)
- `DENDRITE_DB_URL` (SQLite by default, or `postgresql+psycopg://…`)
- `DENDRITE_STORE_URL` (a directory, or an fsspec URL such as `s3://bucket/prefix`)
- `DENDRITE_ENV`, which is mixed into every signature (for example an image digest)
- `DENDRITE_CHECK_READS`: `warn` (default), `error` or `off`, for the undeclared-read guard
- `DENDRITE_GC_GRACE`: seconds (default 3600) during which GC skips freshly written objects
- `DENDRITE_PRESIGN`: `0` stops downloads from a remote store redirecting to signed URLs (when browsers can't reach the store)

## Architecture

The flow of a run spans several modules in `src/dendrite/`:

1. **`experiment.py`:**
   - `Lab` bundles `Metadata` (db) and an `ArtifactStore`.
   - `Experiment.run()` registers a `RunState` in `runtime._RUNS` and calls the pipeline's Prefect flow with `(run_id, input refs)`.
   - `Pipeline._body` sets the `_active_run` contextvar, calls the user function, waits on any `.submit()` futures, and publishes a Prefect markdown summary artifact.
2. **`task.py`, `ArtifactTask`.** Calling a node inside a pipeline runs `_enqueue`, which writes a `queued` node_run. It then invokes a Prefect task (`cache_policy=NO_CACHE`), which runs `_execute`. That method does the following:
   - **Pins:** pinned nodes short-circuit and are recorded as `pinned`.
   - **Splitting arguments:** `_split` separates artifact inputs (any argument containing an `ArtifactRef`, including nested in lists or dicts) from params. Param precedence is experiment `params[node][arg]`, then the call-site value, then the function default.
   - **Signature:** it is `sha256(canonical_json(spec(...)))`.
   - **Resolving:** `_resolve` looks for a verified cache hit, then takes a lease, double-checks the cache, executes under a heartbeat, and calls `db.publish`.
   - **Computing:** `_compute` materialises inputs (`store.load` returns a `Path` or an unpickled value). It then runs the function in a `workdir()`. Returned `Path`s are stored as file/dir artifacts and anything else is pickled as a `value` artifact.
3. **`db.py`.** SQLAlchemy Core tables, with no ORM and no Alembic (`create_all` on connect).
   - The key separation: `experiments` → `node_runs` (per experiment) → `executions` (physical computations shared across experiments) → `execution_inputs` / `execution_outputs` (lineage edges) → `artifacts`.
   - `leases` holds one row per signature that is currently computing.
   - `publish()` commits the execution's success, its edges, metrics, the node_run and the lease release in **one transaction**.
   - SQLite uses two engines: a writer that issues `BEGIN IMMEDIATE`, and a reader. This avoids WAL lock-upgrade `SQLITE_BUSY`.
   - Ancestor and descendant queries are recursive CTEs written in raw SQL that works on both SQLite and Postgres.
4. **`store.py`.** A content-addressed blob store laid out as `objects/<h[:2]>/<h[2:]>`.
   - A directory is a manifest blob, and the directory's hash is that manifest's hash.
   - `LocalStore` uses atomic `os.replace` and hardlinked checkouts. `FsspecStore` handles S3, MinIO, GCS and `memory://`, with a local checkout cache.
5. **`server.py` + `ui/`.**
   - A FastAPI JSON API (`/api/experiments`, `/api/experiments/{id}/dag`, `/api/diff`, `/api/artifacts/{hash}[/lineage|/download]`, `/api/executions/{id}`, `/api/info`).
   - **Shipping the UI:**
     - `ui/` is a React + TS + Vite app (React Flow + elkjs + TanStack Query).
     - It builds into `src/dendrite/ui/dist/`, which is git-ignored and packaged through `[tool.hatch.build] artifacts`.
     - `hatch_build.py` builds a missing bundle with npm when it can. Otherwise it packages without the UI: a git install on a laptop with no Node still works, since the team server serves the UI. `DENDRITE_REQUIRE_UI=1` (releases) fails instead. Editable builds and `DENDRITE_SKIP_UI_CHECK=1` never build it.
     - `create_app` mounts `dist/` with `StaticFiles` *after* the API routes when the bundle exists. Without it, `/` is a short "UI isn't built" page and only the API is served (`/api/info` reports `ui: "missing"`).
   - **Layout:**
     - `ui/src/layout.ts` runs ELK in a web worker. It caches positions per graph shape (node ids plus edges), so status polls never re-lay-out.
     - Edges are drawn from ELK's orthogonal routes by the custom `elk` edge in `Graph.tsx`.
   - **Downloads and previews.** `/api/artifacts/{h}/download` never downloads a remote object whole before replying.
   - It serves a local file if one exists: a `LocalStore` blob, or an existing checkout (`ArtifactStore.local_path`).
   - Otherwise it streams `open_object` in chunks and honours a single `Range`.
   - With `?redirect=1` and a store that can sign (S3), it answers 307 to a presigned URL. The UI adds it (`direct()` in `api.ts`) only for links and `<video>`/`<audio>`, which don't need CORS; the in-page viewer's fetch always goes through the server.
   - On remote stores the detail view checks only a directory's manifest (`verify(deep=False)`) and sends `auto_preview_bytes`. Larger files wait for "Load preview". Cache hits still verify every member.

- **Selection** lives in the hash URL (`?sel=node:|input:|artifact:|exec:<id>`). `App.tsx` derives both the graph highlight and the detail panel from it.
  \- **Labels:** lineage artifact labels (`label`, `producer_node`, `role`) and structured usage `roles` are computed in Python (`Lab.lineage`, `Metadata.experiments_using_artifact`), not in the UI.
  \- **Headless screenshots:** headless Chrome's `--virtual-time-budget` stalls the ELK worker. Use `uv run python scripts/screenshot.py OUT.png URL [wait_s]` instead, which captures in real time over CDP and prints console errors.

06. **`viz.py` / `cli.py`.** Mermaid renderers and the argparse CLI.
07. **Drafts and plans.**
    - **Drafts.** `status='draft'` is the only editable experiment state.
      - `Experiment.update()` / `discard()` and the draft→running transition in `run()` are compare-and-set updates (`Metadata.update_experiment_if`).
      - There is no schema change: `draft` is just a status value.
    - **Plans.** `Experiment.plan()` runs `pipe.fn` directly, without the Prefect flow or `_body`, under a `RunState(plan=True)`.
      - `ArtifactTask.__call__`/`submit` branch to `_plan` *before* `_enqueue`, so nothing is written.
      - `_plan` uses the same `_split`/`spec`/`sha256_json` as `_execute`, then stops at `_valid_hit`.
      - A miss returns placeholder refs (`kind="pending"`, `hash="plan:<node>.<out>"`). Downstream steps become `run`/`upstream`.
      - `store.load` on a placeholder raises `PlanIncomplete`, which the plan reports as `blocked_by`.
    - **Freshness.** `runtime.refresh_modules()` reloads tracked pipeline/step modules whose mtime changed. Without it, a long-lived server would plan with stale code fingerprints.
    - **API.** The draft and plan endpoints live in `server.py`. Planning needs the pipeline module imported (`--import`); otherwise `/plan` answers 409 `pipeline_not_imported`.
    - **Float params.** JSON can't distinguish `1` from `1.0`, so `PATCH` takes a `floats` list. The UI's `components/Draft.tsx` sends float-typed keys in it.
08. **`guard.py`.** The undeclared-read tripwire.
    - It is one permanent `sys.addaudithook`, idle unless the `_active` contextvar is set.
    - `task._compute` sets that contextvar around the step function only. It reports read-only opens of files under `Settings.project_root` (the cwd when the Lab is created), excluding the workdir, `DENDRITE_HOME`, store paths, interpreter prefixes and code files.
    - In `error` mode a violation fails the execution even if the step swallowed the exception.
09. **`analysis.py`.** Static checks over `ast`. The analysed code is never executed and never feeds a signature.
    - **`check_step(task)`** runs from `ArtifactTask.__init__`.
      - Findings are emitted with `warnings.warn_explicit` as `StepDefinitionWarning`, located at the offending file:line, and stored on `task.diagnostics`.
      - `ignore=` filters codes.
      - `task._STEPS` registers every step, for `dendrite check`.
    - **`pipeline_shape(source, resolve)`** is a def-use walk of the pipeline body.
      - `resolve` maps a name to a module global (`fn.__globals__`), looked up lazily, because steps may be defined below the pipeline.
      - `if`/`else` frames mark nodes `conditional`. A step in exclusive arms merges into one node whose conditions are the shared prefix.
      - Loops and comprehensions mark nodes `repeated`.
      - Non-step calls that receive pipeline values become `dynamic` nodes, except builtins and logging.
      - Same-module helpers are expanded one level.
    - **`Pipeline.shape(source=None)`** works on loaded code or unsaved text. Lines are relative to the text; `first_line` locates it in the file.
    - **`Experiment.check()`** compares params and pins against the shape. `unknown-step` drops to a warning when the shape has dynamic regions.
    - **Server.**
      - `/api/pipelines/{name}/shape` (GET for loaded code, POST for unsaved text) calls `runtime.refresh_modules()` first. GET also saves the pipeline's source as a snapshot, so the Code view reuses `GET`/`PUT /api/sources/{hash}` for viewing and saving.
      - Unsaved Code-view edits register in `ui/src/unsaved.ts`, and `App.tsx`'s `navigate` asks before switching view or experiment.
10. **`sources.py`.** Source snapshots of step functions.

- They live in `code_snapshots` / `execution_sources`, outside the signature, so they never change cache keys.
- They are recorded when an execution starts.
- `disk_status` reports `same` only when the snapshot text occurs exactly once in the recorded file.
- `write_source` writes only to that recorded path. It checks the client's file hash (409 on mismatch) and replaces atomically.
- `PUT /api/sources/{hash}` is gated by `create_app(allow_edit=...)`. `dendrite ui` enables it on loopback hosts or with `--allow-edit`.

11. **`storage.py`.** Storage accounting and reclamation, exposed as `Lab.storage()/gc()/evict()/clean()` and `Experiment.delete()/keep()`.

- **Reachability defines what is kept.** Roots are each existing experiment's input hashes, pin hashes and node-run outputs, plus the members of directory manifests. Anything else in `objects/` is collectable. There is no reference counting.
- **Metadata is never deleted by reclamation.** `Lab.delete(ids, forks=…)` removes only `experiments`, their `node_runs` and `experiment_flags` rows (`Metadata.delete_experiments`, one transaction, children first). With `forks="keep"` it first re-parents surviving forks to the nearest surviving ancestor, in the same transaction. Executions keep their `experiment_id`, which the UI labels "deleted experiment".
- **Stuck runs.** `Experiment.mark_stopped()` turns a `running` experiment left by a crashed process into `failed`, which unblocks the busy guard.
- **Safety.** Blobs written within the grace period are skipped. Applying GC or evict, and `clean()`, refuse while an experiment is `running` or a lease is held (`StorageBusy`), unless forced. Eviction refuses pinned outputs (`EvictRefused`). `gc(hashes=…)` intersects a fresh report with the previewed hashes.
- **Checkouts.** Deleting blobs also drops their checkouts (`store.drop_checkouts`), because local checkouts are hardlinks that would keep the bytes alive.
- **Age-based GC.** `gc(older_than=…)` treats node-run outputs of executions last used before the cutoff as non-roots. It spares what kept experiments, any pin, or each pipeline's newest completed experiment reach.
- **Stores.** `ArtifactStore.iter_objects()` lists `(hash, size, mtime)`. `FsspecStore` caches dir manifests locally under `cache/manifests/`.
- **Server.** The server caches the inventory for 30 s (`?refresh=1`). Every mutation endpoint requires `allow_edit`; previews (`/api/gc`, `/evict` without `apply`) don't. The UI is `ui/src/components/Storage.tsx`, at `#/storage`.

12. **Live graph.**

- **Progress.** `runtime.progress` writes `execution_progress` rows, throttled by `PROGRESS_INTERVAL`, through `ExecContext.db`. A running node run carries its `execution_id`, so `dag()` can attach `progress`.
- **Body reads.** `Lab.load` calls `runtime.note_read`, which records `pipeline_reads` when a run is active and no step is executing. Plans keep reads in `RunState.reads` only. `dag()`/`plan()` return them with each read's producer.
- **Push.** `GET /api/events` is Server-Sent Events. It compares `Metadata.change_tokens()` every `EVENT_INTERVAL`, because runs happen in other processes. `useLiveUpdates` in `ui/src/queries.ts` invalidates only the named experiments, and polling drops to a 15 s safety net while connected.
- **Grouping.** It is UI-only (`ui/src/fanout.ts`). Nodes named `step[key]` group by `step`, and groups of `COLLAPSE_AT` or more start collapsed.

13. **Stages.**

- **Context.** `runtime.stage` is a context manager and decorator over the `_stage` contextvar. `ArtifactTask.stage_path()` (enclosing stages, else `stage=`) is read at `_enqueue`, stored in `node_stages`, and in plan entries. `dag()`/`plan()` nodes carry `stage`.
- **Static shape.** `analysis.py` pushes `("stage", name)` frames for `with stage("…")` and for `@stage` helpers (expanded up to depth 3), so shape nodes carry `stage` names.
- **View.** `pipeline_views` stores the UI overlay (`assign` step→stage, `styles` stage→label/description/color) behind `GET`/`PUT /api/pipelines/{name}/view`.
- **UI.** `ui/src/stages.ts` combines code stages, the view and fan-out into one member map (outermost collapsed stage wins, then a collapsed fan-out group). Editing lives in `components/StageDetail.tsx`.
- **Sub flows.**
  - `DagView.framed()` turns expanded stages and fan-outs into frame nodes (`frame`, `parent`).
  - `layout.ts` lays them out with ELK's `INCLUDE_CHILDREN`. Edge sections are relative to their `container`; fan-out frames use `rectpacking`.
  - Expanded fan-outs draw at most `SHOW_AT_MOST` items (`sampleItems`), and the rest map to `more:<step>`.
  - Items' edges attach to their `fan:<step>` frame. Never draw every item of a fan-out.

14. **Hosted mode** (`hosted/`, `remote.py`, OpenSpec `hosted-server`).

- **Choosing a backend.** `config.Settings.resolve` picks direct, hosted or local mode. In order: arguments, `DENDRITE_DB_URL`/`DENDRITE_STORE_URL`, `DENDRITE_SERVER`+`DENDRITE_PROJECT`, the nearest `dendrite.toml`, local. In hosted mode `Lab` holds `remote.HttpMetadata` and `remote.ServerStore`. Everything above them (tasks, signatures, verify, plans) is unchanged.
- **The server** (`hosted/app.py`, `dendrite server run --config server.toml`).
  - Each project is a Postgres schema holding the unchanged core tables, plus a blob prefix.
  - The `dendrite_control` schema (`hosted/control.py`) holds users, projects, memberships, tokens (stored hashed), sessions, device logins, attribution, value previews and URL-signing logs.
  - `/api/v1/projects/{p}/rpc/{method}` runs one method from `wire.REMOTE` on the project's `ServerMetadata` as the caller.
- **Permissions** (`hosted/authz.py`).
  - `ServerMetadata` checks every write against the `CALLER` contextvar: role, ownership, leases bound to the caller (`Caller.tag`), and committed blobs before `publish`/`register_artifacts`.
  - The UI mount (`hosted/ui.py`, `/p/{project}/`) reuses `create_app(hosted=…)`. Only storage reclamation needs a path rule (`ADMIN_ROUTES`).
- **Blobs** (`hosted/blobs.py`).
  - `blobs/missing` deduplicates. S3/MinIO uploads are presigned PUTs with `ChecksumSHA256` + `If-None-Match: *` (multipart: per-part checksums). `verify_uploads = "hash"` stages them and re-hashes on the server.
  - Blobs ≤256 KB travel inline in `blobs/missing`. GCS uses V4 signed URLs from `hosted/gcs.py` (IAM `signBlob` without a key file), with `x-goog-if-generation-match: 0` and the client's CRC32C compared on commit. Stores that can't presign (a local directory, `memory://`) are proxied through the server, which hashes them.
- **Sign-in** (`hosted/auth.py`).
  - Browser: GitHub, generic OIDC, or `dev` (localhost only), giving a session cookie plus CSRF header.
  - CLI: a device flow run by the server itself. The token is minted on the poll, so no secret is ever stored in plain text. `remote.device_login` prompts automatically on the first call only when interactive; otherwise it raises `NotLoggedIn`.
- **`dendrite push`** (`push.py`): `Metadata.export_bundle` on the local lab, the blobs through `ServerStore._put_missing`, then `import_bundle` on the server in one transaction. Existing experiments are skipped; an execution with an existing id, or a successful cacheable one with the same signature, is reused. Executions whose blobs were evicted locally are left out.
- **Round trips.** `HttpMetadata` batches "queued" node-run writes and progress, and flushes before any other call. Clients drop idle connections at `remote.KEEPALIVE` (2 s), below the server's 30 s, or a reused connection can lose a request. `ArtifactStore.prefetch` batches a step's input URLs.

15. **Run telemetry** (`telemetry.py`, OpenSpec `run-telemetry`).

- **fd capture.** `telemetry.step_started/step_finished` keep `_FD` (an `_FdCapture`) told which steps run. While exactly one does (POSIX, not in ipykernel, `DENDRITE_CAPTURE_FD` not `0`), `_FdSession` dup2s pipes over fds 1/2 with reader threads that forward to the saved fds and into that step's log; `_Tee` and the logging handler skip what the pipes will see (`_fd_backed`), so nothing is logged twice. `step_finished` runs before the final flush, so all fd output is read.
- **Big logs.** The final flush of a log over `DENDRITE_LOG_INLINE` writes it to the store (`Telemetry._to_store`) and `Metadata.attach_log` swaps the chunks for an `execution_log_blobs` row; `/logs` then serves its last 256 KB with `stored`. Stored logs and samples are `attachment_hashes()`, roots of the experiments that ran or reuse their execution.
- **Capture.** `task._compute` creates a `telemetry.Telemetry` on the `ExecContext` and calls `telemetry.install()`, which wraps `sys.stdout`/`sys.stderr` in `_Tee` proxies (re-wrapping if something replaced them) and adds one root `logging` handler. Writes are routed by the `_exec` contextvar, so concurrent steps keep separate logs; `_local.busy` stops the database layer's own logging being captured. `dendrite.run` streams a subprocess through the proxy.
- **Buffering.** Chunks and metrics flush at most every `LOG_INTERVAL`, plus a final flush before publish or fail (the traceback is written to stderr first). Past `DENDRITE_LOG_MAX / 2` output goes to a rolling tail, kept live in the `seq = -1` row and written after a truncation marker at the end. `_compute` still returns every metric to `publish`, which rewrites them in its transaction (delete-then-insert per key and step), so a failed live flush never loses one.
- **Metric times.** `Telemetry.metric` records when each point was logged; `write_telemetry(times=...)` stores them in `metric_times`, joined into `scalars()` as `t` (null for points written only by `publish`). Charts (`ScalarCharts`) share smoothing, step/time axis, log scale and zoom; `ExperimentScalars` overlays other experiments via `mergeSources`.
- **Tables.** `execution_logs`, `execution_samples` (artifacts in the store, never outputs; history capped at write time), `environments` + `execution_environments` (one row per distinct snapshot), `execution_activity` (feeds `change_tokens`). Metrics are upserted into `metrics` while running.
- **Environment.** `telemetry.snapshot` memoises the static part per process and git state for `GIT_TTL`; never environment variables; remote credentials stripped.
- **Storage.** Sample hashes are roots of every existing experiment that ran or reuses their execution.
- **Hosted.** `HttpMetadata.write_telemetry` batches on the progress flusher; `ServerMetadata` checks execution ownership for every telemetry write and committed blobs for samples.
- **UI.** `components/Telemetry.tsx` (`RunTelemetry` tabs in `NodeDetail`, `ExperimentScalars` on the Metrics page); pure helpers in `ui/src/telemetry.ts` (ANSI, carriage returns, `title/series` grouping, smoothing, min/max downsampling). Charts are plain SVG.

16. **Artifact names** (OpenSpec `artifact-organisation`).

- **Tables.** `artifact_names` (one current target per name, with the experiment/node/output it was named from) and the append-only `artifact_name_history` (`moved` | `removed`). `Metadata.set_name` validates `NAME_RE`, refuses moving a name without `move` (`NameConflict` `name_taken`) and does a compare-and-set on `expect` (`name_moved`), in one transaction with the history row.
- **Resolution.** `Lab.artifact` and `Lab.ingest` treat a string starting with `@` as a name, never a path. Inputs and pins store the resolved hash, so a moved name never changes frozen config. `Lab.resolve_target` parses `EXP:NODE.OUTPUT`, `EXP:NODE`, `EXP:INPUT`, `@name`, a hash or prefix.
- **Storage.** `storage.NAMED` (`"@names"`) is a root group of every named hash: never collected, excluded from "exclusive"/"shared", and `evict` refuses named outputs unless forced.
- **Ownership and tags.** `artifact_names.set_by_id` (the server's user id) decides who may change a name; `set_by` is the display label. `tags` is a JSON list, kept on a move unless replaced. `_add_name_columns` adds both to tables made by pre-release builds. Compare uses `GET /api/compare` (node runs and `Experiment.diff_params`, no DAGs).
- **API.** `GET/PUT/DELETE /api/names/{name}`, `GET /api/names/{name}/history`; DAG, artifact and lineage responses carry a `names` map (hash → names) from `_with_names`. Names change the tree change-token, so SSE refreshes names, DAGs and artifacts.
- **Hosted.** `ServerMetadata.set_name/remove_name` record the caller's label as `set_by` and require the setter or an admin to move, re-note or remove a name. `export_bundle` carries current names; `import_bundle` adds missing ones and reports conflicts (`names_skipped`) without overwriting.
- **Artifacts tab.** `#/exp/:id/artifacts?kind=&q=&cmp=node.output` (filters in the URL). `ui/src/artifacts.ts` builds rows from the DAG (inputs, then outputs in `seq` order; source computed/reused/pinned from status and `execution_experiment`), groups stage → step, and picks Compare candidates (self, parent, direct forks, capped at 8). `components/Artifacts.tsx` renders it; Compare fetches each candidate's DAG and `/api/diff`, and collapses identical hashes. No new endpoint.
- **UI.** `components/Names.tsx`: `NameControl` (★ chips and ☆ Name… with a "Move it here" flow on `name_taken`) in `ArtifactDetailPanel`, and `NamesPage` at `#/names`. Output ports and the node panel show `★ @name`.

17. **Eager steps and cache control** (OpenSpec `easy-adoption`).

- **Eager calls.** `ArtifactTask.__call__` with no `_active_run` goes to `_eager`: arguments are converted (`_eager_input`: `Path` → ingested ref with a (path, size, mtime) memo, paths an eager call returned → their ref via `_CHECKOUTS`, strict JSON → params, anything else → `put_value`), the process's session `RunState` (`runtime.session(lab)`, pipeline `"session"`, created lazily against `runtime.default_lab()` / `dendrite.use`) becomes active for the call, and `_enqueue` + `_execute` run directly, without Prefect. Repeated calls use `named(n)`. Outputs come back loaded (`_eager_output`). `dendrite.step` is `artifact_task`.
- **Rerun.** `RunState.rerun` (`True` or names; `reruns(node)` matches `step[k]` by its step). `_resolve` skips the lookup for reruns, then compares outputs with the replaced hit and warns `NondeterministicStep` plus a log line.
- **`DENDRITE_CACHE`** (`runtime.cache_mode()`, read per call): `off`/`bypass` act as rerun-all in pipelines; `readonly` looks up but publishes `cacheable=False` (`find_success` only returns cacheable executions); `bypass` makes eager calls plain function calls.
- **Builtins** fingerprint as `module.qualname@<dist version>`; positional-only parameters are passed positionally.

## Invariants to preserve

- Signatures must never include experiment ids or flow-run ids. That is what makes cross-experiment reuse work.
- The signature's `op` is the step's identity (`ArtifactTask.op`), not its node name. Renamed copies (`step.named(key)`, `with_options(name=...)`) keep it, so fan-out items doing identical work share results. Named copies are created once per key, skip static checks and aren't registered in `_STEPS`.
- Only params a node actually consumes are hashed.
- Only `ArtifactRef`s cross Prefect task boundaries. Never pass large data through Prefect.
- A cache hit must `store.verify()` its outputs, including every member of a directory manifest. A missing blob means recompute.
- Experiment config (params, inputs, pins) is frozen at its first run. Before that, only `draft` experiments are editable. Re-running only replaces node_runs.
- **Reclaiming space never risks a wrong result.** It may only delete blobs, never metadata rows or anything a cache hit could trust without `verify()`. Anything deleted must recompute when needed. `tests/test_storage.py` covers this.
- Progress, body reads, stages and run telemetry (logs, metrics, samples, environment) are display-only: they never enter a signature.
- **Plans are side-effect free.** A plan never writes node_runs, executions, blobs, leases or pipeline reads, never touches Prefect, and never calls step functions. `tests/test_plan.py::test_plan_writes_nothing` checks this.
- **A hosted server never unpickles.** Value previews come from the text the producer recorded (`value_previews`). `tests/test_hosted.py::test_the_server_never_unpickles` checks this.
- **Hosted publish needs committed blobs.** No execution or artifact row may reference a blob the server hasn't confirmed is stored at its content address.
- **Step contract** (README, "The step contract"):
  - The pipeline body only wires steps together.
  - Step outputs depend only on what the signature hashes.
  - Params are JSON values, hashed exactly, so `1` and `1.0` give different signatures.

## Known limitations

- **In-process only.** Tasks find their experiment through the in-process `runtime._RUNS` registry. Prefect deployments, remote workers, and process, Dask or Ray task runners don't work yet.
- **Narrow code fingerprint.** The fingerprint covers only the node function's own source (`inspect.getsource`). Callers bump `version=` when helper functions change.
- **Pickles.** `value` artifacts are pickles, and `load()` and the UI preview unpickle them.
- **The read guard is best-effort.** It sees only Python-level `open`/`os.open`. It misses C `fopen`, subprocesses, threads started by the step, and anything outside the project directory.
- **gcsfs's experimental gRPC mode is off.** `dendrite/__init__.py` sets `GCSFS_EXPERIMENTAL_ZB_HNS_SUPPORT=false`: otherwise gcsfs asks Google's API for each bucket's layout over gRPC, which ignores `endpoint_url` and stalls where gRPC is blocked.
- **Hosted mode, per replica.** Upload sessions, OAuth sign-ins and replies to retried writes live in the control database, so replicas can share the load. Token and role lookups are cached per process for `AUTH_CACHE_SECONDS`, so a token revoked on one replica keeps working on another until its cached entry expires.
- **Plans can be incomplete.** They can't predict branching on the *contents* of a result that hasn't been computed. Downstream `run`/`upstream` is an upper bound: an unchanged upstream output still lets downstream reuse.

## Tests

- `tests/toy.py` defines the toy pipelines and a `CALLS` counter of real executions. Tests assert on that counter to prove cache reuse.
- Pipelines and tasks are registered by name at import time (`_PIPELINES`), so `Experiment.pipeline` needs the defining module imported.
- `conftest.py` starts one session-wide `prefect_test_harness`.
- **Hosted tests.** The `hosted` fixture runs a real server in a thread on the test Postgres and moto. `tests/test_hosted.py` covers roles, leases, uploads, login and the UI gate. `DENDRITE_TEST_REMOTE=1` runs the whole suite with `lab` backed by a server project; tests that reach into local internals are marked `@pytest.mark.local_only("why")`. moto doesn't enforce upload checksums, so the fixture uses `verify_uploads = "hash"`.
- UI tests live next to the code as `ui/src/**/*.test.ts(x)`. Builders are in `ui/src/test/fixtures.ts`, and `renderWithQuery` gives a fresh QueryClient. Mock the network with `vi.spyOn(api, ...)`. `layout.ts` creates the ELK worker lazily, so graph modules import cleanly under jsdom.

## OpenSpec

Spec-driven changes live in `openspec/`:

- `openspec/config.yaml` holds project context.
- `openspec/specs/` holds the baseline specs from the archived `add-artifact-lineage` change.
- `openspec/changes/draft-experiments/` is drafts, plans and the step contract (phase 1 of the editing UI).
- `openspec/changes/storage-management/` is the storage inventory, delete, evict, GC and clean-up change.
- `openspec/changes/live-graph/` is live progress, pushed updates, fan-out groups and pipeline-read edges.
- `openspec/changes/pipeline-stages/` is stages in code and in the UI, and the stage overview.
- `openspec/changes/hosted-server/` is the proposed hosted mode: a server that owns Postgres and the bucket, login, projects and roles, and presigned blob access. Not implemented yet.
- `openspec/changes/run-telemetry/` is console capture, live scalars, samples and environment per execution (implemented).
- `openspec/changes/artifact-organisation/` is artifact names (`@name`, implemented) and the per-experiment Artifacts tab.
- `openspec/changes/easy-adoption/` is steps outside pipelines (sessions), `dendrite.step(fn)`, rerun and `DENDRITE_CACHE` (implemented).
- `openspec/changes/react-flow-ui/` is the React Flow UI change. Archive it with `openspec archive react-flow-ui` once it has been reviewed.
- Use `/opsx:propose`, `/opsx:apply` and `/opsx:archive` for new work.
- Validate with `openspec validate <change>`.
