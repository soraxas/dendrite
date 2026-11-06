## Context

A step call inside a running pipeline goes `__call__` → `_enqueue` (writes a queued node run) → the Prefect task → `_execute` → `_resolve` (cache hit, or lease → `_compute` → publish). The run is found through the `_active_run` contextvar and `runtime._RUNS`. Outside a pipeline, `runtime.active()` raises.

## Decisions

### 1. Eager calls reuse `_execute` under a session `RunState`, without Prefect

When `_active_run` is unset, `__call__` converts the arguments, makes sure the process's session experiment exists (`runtime.session(lab)`), and sets `_active_run` to that session's run id for the duration of the call. It then calls `_enqueue` and `_execute` directly, not through `_prefect_task`. `_execute` falls back to `logging.getLogger("dendrite")` when there's no Prefect run logger. That way signatures, leases, verification, telemetry and publishing are the same code as in a pipeline.

*Alternative:* a hidden one-step Prefect flow per call. It would cost hundreds of milliseconds per call, and it would mean nested flows in user code.

### 2. Argument conversion

The arguments are walked like `_map_refs`:

- **Paths:** a `Path` becomes a ref via `lab.ingest`. A per-process memo of (resolved path, size, mtime) → ref avoids hashing a file twice. Paths returned by earlier eager calls are mapped straight to their refs.
- **JSON values** (anything `canonical_json` accepts) stay params.
- **Anything else** becomes a value artifact (`store.put_value`). Its hash is the pickle's hash, which can give a spurious miss but never a wrong hit.

### 3. Sessions are ordinary experiments

A session is created lazily: `pipeline = "session"`, `name = "<script> · <HH:MM>"`, status `running` while a step executes and `completed` after it. It isn't held `running` for the whole process, so storage reclamation isn't blocked by an idle script. External inputs are added to the experiment's `inputs` (`<node>.<arg>`) so the graph shows them. Repeated calls use `named(str(n))` copies, which keep the op and therefore reuse. `dendrite.use(lab)` sets the default lab and starts a new session.

### 4. Rerun

`RunState.rerun` is `True` or a set of node names; `Experiment.run(rerun=...)` sets it, as does `step.rerun(...)` for one eager call. In `_resolve`, a node to rerun skips the cache lookup but still takes the lease, so concurrent runs don't duplicate the work.

After computing, if a previous verified hit existed, its output hashes are compared with the new ones. A difference warns with `NondeterministicStep` (a `UserWarning`) and writes a marker line to the step's log. Because `find_success` orders newest first, the rerun's execution becomes the one reused afterwards.

### 5. `DENDRITE_CACHE`

`Settings.cache_mode` is read once per lab:

- `off` behaves like `rerun=True` for every step.
- `readonly` looks up hits but computes misses without a lease and publishes them with `cacheable=False`, so they are recorded but never reused.
- `bypass` affects eager calls only: they return `fn(*args, **kwargs)` with no recording. Inside a pipeline, `bypass` acts as `off`.

### 6. Builtins

`code_fingerprint` falls back to `module.qualname@<distribution version>` when there's neither source nor `__code__`.

## Risks

- **Hidden inputs.** Eager mode invites wrapping functions that read globals or config. The read guard still warns about undeclared file reads; the docs say what else to watch for.
- **Unstable value hashes.** Value inputs hashed by pickle can miss for objects whose pickles aren't stable. This is documented, with "make it a param or a file" as the fix.
