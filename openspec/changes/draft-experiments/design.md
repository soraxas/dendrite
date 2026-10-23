## Context

Experiments are created frozen, with status `created`, and only gain node_runs by running in Python. A run goes through these steps:

1. `Experiment.run()` calls `Pipeline.flow`, a Prefect flow.
2. The flow runs `Pipeline._body`, which:
   - records `flow_run_id`
   - sets the `_active_run` contextvar
   - calls the user function
   - waits on futures
   - publishes a Prefect summary artifact
3. Each step call goes `ArtifactTask.__call__` → `_enqueue`, which writes a `queued` node_run. Then the Prefect task runs `_execute`, which goes pin check → `_split` → `spec` → `sha256_json` → `_resolve`.

The UI server is long-lived, and with `--import` it holds the user's pipeline module in memory.

## Goals / Non-Goals

**Goals:**
- Make a draft that is editable until it first runs, without changing the DB schema.
- Predict each step's outcome using exactly the code path that computes real signatures, so a prediction can never disagree with the real cache key.
- Never let a plan write anything: no node_runs, executions, blobs, leases or Prefect state.
- Edit params and pins in the UI, and see the cost of the change before running.

**Non-Goals:**
- Starting runs from the UI (phase 2).
- Editing the pipeline's steps or wiring.
- Sweeps.
- Swapping in another experiment's result.
- Removing the `--import` requirement.

## Decisions

### D1. Drafts are a status value, not a new table
`experiments.status` gains the value `draft`, and every other column is reused.
- **Editing:** `Experiment.update()` edits a draft with `UPDATE … WHERE id=? AND status='draft'`, a compare-and-set. If zero rows change, it raises `NotADraft`.
- **Running:** `run()` changes `draft` or `created` to `running` with the same kind of compare-and-set. That is the moment the config freezes, so a UI edit racing `dendrite run` either lands before the freeze or fails.
- **Discarding:** `discard()` deletes a draft that has no children.

*Alternative:* a separate `drafts` table. Rejected because the fork tree, the sidebar and the parent links would all need to merge two sources, and `create_all` cannot migrate existing columns anyway.

### D2. Plan mode bypasses Prefect entirely
`Experiment.plan()` works as follows:
- It registers a `RunState(plan=True)` with its own empty `called` set, then sets `_active_run` and calls `pipe.fn(**input_refs)` directly. It does not call `pipe.flow` or `_body`.
- `ArtifactTask.__call__` and `submit` check `runtime.active().plan` *before* `_enqueue`, and route to `_plan(call)`:
  - `__call__` returns the step's refs, or placeholders.
  - `submit` returns a `PlanFuture` whose `result()` returns those same refs.
  - Before `_split`, `_plan` unwraps any `PlanFuture` in its arguments. Prefect does this for real futures.

`_plan` then runs the same checks as `_execute`:
- **Pinned step:** the outcome is `frozen`, and the step returns the pinned refs.
- **Any input is a placeholder:** the outcome is `run`, with reason `upstream`. The step returns placeholders.
- **Otherwise:** `_split` and `spec`, then `sha256_json`, then `_valid_hit`.
  - A hit means `reuse`, with the execution id, the experiment it came from, and how long it took.
  - A miss means `run`, with a reason. The reason comes from comparing against the parent's node_run for the same step (code, then params, then inputs, then env), or `new` if the parent has none, or `missing_blob` if the signature matches but verification fails.

**Placeholders.** A placeholder is an `ArtifactRef` with `hash="plan:<node>.<output>"`, `kind="pending"` and size 0.
- `Lab.load` or `store.load` on a placeholder raises `PlanIncomplete`.
- `plan()` catches that error and returns the outcomes recorded so far, plus `complete: false` and the step that blocked.
- Any other exception in the body is also caught and reported as `error`.

**Estimated duration.** The estimate is the duration of the most recent successful execution of the same op. It prefers an execution with the same signature, then the parent experiment's execution, then any execution of that op. If there is none, the estimate is `null`.

*Alternative:* derive the plan from the previous run's DAG. Rejected because it can't see branching on params, and it would need its own hashing logic.

### D3. The plan's output looks like the DAG
`plan()` returns:
- `experiment` and `complete`
- `blocked_by`, `error` and `env` (the server's `DENDRITE_ENV` and package fingerprint, so a runner mismatch is visible)
- `nodes` and `edges`, in the same shape as `/api/experiments/{id}/dag`

Each node adds:
- `plan`: `reuse | run | frozen`
- `reason`: `code | params | inputs | env | upstream | new | missing_blob | uncached`
- `estimate`
- `reuse_from`: `{execution_id, experiment_id, duration}`
- `param_schema`

Edges come from refs flowing into inputs, including placeholders. That lets `DagView` render a plan with only a status mapping.

### D4. The param schema comes from the live code, not from the DB
`param_schema` lists each non-artifact parameter of the step function:
- `name`
- `default`, as JSON when it can be encoded
- `annotation`, as a string
- `json_type`, taken from the annotation or else from the type of the default
- `value`, the effective value for this experiment
- `inherited`, the parent's effective value

The schema needs the module to be imported, and so does planning. So the schema isn't recorded at run time, which saves a table.

*Alternative:* record the schema in node_runs. That would need a column (no migrations) and could go stale.

### D5. The module is reloaded when the code changes
`ArtifactTask.code` is computed once at import, so a long-lived server would plan with old fingerprints after a code edit. That would show "Reused" for a step the next run will recompute.
- Before each plan, `plan_lab` checks the mtimes of the source files of the imported pipeline modules.
- If any changed, it calls `importlib.reload` on the changed modules, step modules first, then the pipeline module. This re-registers `_PIPELINES` and the tasks.
- Every plan request runs the check first, so a save from the source editor is picked up on the next plan.

*Alternative:* recompute `code_fingerprint(fn)` on every plan. Rejected because it misses edits that add or remove steps in the pipeline body.

### D6. Param values round-trip exactly
`canonical_json` distinguishes `30000`, `30000.0` and `"30000"`, so they give three different signatures. Because of that:
- The API accepts and returns typed JSON values.
- The UI form turns input into the `json_type` from the schema: integers stay integers, and floats only appear when the user types a decimal.
- Edits arrive as dotted overrides through the existing `_apply_overrides`. A separate `reset: ["node.arg"]` list returns a key to the parent's value, or drops the override when the parent has none.
- The required test: re-save every param unchanged, and the plan must still be all-reuse.

### D7. Endpoints, gated like source editing
Every endpoint that changes something answers 403 unless `create_app(allow_edit=True)`.

| Endpoint | Behaviour |
|---|---|
| `POST /api/experiments/{id}/fork` | Body `{name?}`. Creates a draft whose parent is `{id}`, with the same config. |
| `PATCH /api/experiments/{id}` | Body `{params?, reset?, floats?, pins?, inputs?, name?}`. `reset` lists `node.arg` keys to return to the parent's value; `floats` lists keys whose whole-number values must be stored as floats (JSON can't tell `1` from `1.0`). Edits a draft; 409 if it isn't one. `pins` maps a node either to `"parent"` (freeze it to the parent's outputs) or to `null` (unfreeze). |
| `DELETE /api/experiments/{id}` | Discards a draft; 409 if it isn't a draft or has children. |
| `GET /api/experiments/{id}/plan` | Read-only, allowed for any experiment. Answers 409 with `code: "pipeline_not_imported"` and a hint when the module isn't loaded. |
| `GET /api/info` | Adds `plan: bool`, which is true when at least one pipeline is importable. |

### D8. The UI's draft mode
- **Banner and background:** a draft has its own banner and background tint, the way lineage mode does. The banner reads "Draft — edits are saved as you go. Nothing runs until you run it", and shows the copyable command `dendrite run <id> --import <module>` and a Discard button.
- **Graph:** it renders from `/plan` instead of `/dag`. Three new legend entries are shown only while in draft mode: Reused (the cached colour), Will run (a new `--plan-run` colour), and Will run, upstream changed (the same colour, dashed). Frozen reuses its existing colour.
- **Side panel:** the step detail panel shows the param form, with the inherited value and a ↺ reset next to each field, plus a Freeze toggle.
- **Saving and replanning:** edits go out as a debounced PATCH, then the plan is fetched again. The previous plan stays on screen while the new one loads.
- **Summary line:** the toolbar summarises the plan, e.g. "Will run 2 of 4 steps · about 14 min", or "Nothing to run — every step is reused".
- **Fork button:** "Fork…" appears on any experiment that isn't a draft, and is disabled with a tooltip when editing is off.
- **Statuses:** `draft` is added to `EXPERIMENT_STATUS`. Sidebar `outcome()` shows "Draft · will run N of M" from a cheap cached plan count, or "Draft". The glyph is a dashed ring.

### D9. The step contract is documented, not enforced
Drafts make the pipeline body run more often (on every plan), and the editing UI encourages people to trust "Reused". So the implicit contract is written down, in the README and here:

- **Pipeline body.** It only wires steps together. It may branch on params, but it does no work and has no side effects. This is the same role as Temporal's deterministic workflow code.
- **Steps.** They may have side effects, like activities, but must be *hermetic in what they declare*. Their outputs depend only on what goes into their signature: code, consumed params, artifact inputs, env and packages.
- **Params.** They need no type hints. They must be JSON-serialisable, which `canonical_json` already enforces, and they are hashed by exact value.

*Alternative:* enforce purity, through sandboxing or by forbidding I/O. Rejected for two reasons. Python can't do it cheaply, and steps legitimately touch GPUs, scratch space and the network. Temporal needs determinism for replay to work at all, while dendrite needs it only for cache correctness, and `cache=False` already covers steps that are inherently fresh.

### D10. A best-effort guard for undeclared reads
A process-wide `sys.addaudithook` is installed once, the first time a step executes. Hooks can't be removed, so it checks a contextvar `_reads_guard` that `_compute` sets for the duration of the step function. On an `open` event with a read-only mode, it flags the path when all of these hold:

- the path resolves under the project root, which is the current directory when the lab is opened
- it isn't under the step's workdir
- it isn't under a checkout path of a declared input
- it isn't under `DENDRITE_HOME`, a virtualenv or site-packages
- it isn't a code file (`.py`, `.pyc`, `.pyi`, `.so`, `.pyd`, `.pth`, `.dylib`), which covers imports and `inspect` reading source

Each `(step, path)` pair is reported once. The `DENDRITE_CHECK_READS` setting picks the response:

- `warn` (the default): a `UndeclaredReadWarning` plus a Prefect log line
- `error`: raise `UndeclaredRead`, which fails the execution
- `off`: no check

Plan mode never runs step functions, so the guard doesn't apply there.

*Limits, stated plainly:* the guard sees Python-level `open` and `os.open` only. It doesn't see C extensions calling `fopen`, subprocesses, or threads the step starts, which don't inherit the contextvar. Reads outside the project root, such as `/data/...`, aren't flagged, to keep the noise down. It is a tripwire, not a sandbox.

*Alternative:* record the reads on the execution and show them in the UI. Deferred, because it needs a table. The warning comes first.

## Risks / Trade-offs

- **Pipeline bodies may have side effects (prints, file writes) that also happen at plan time.** → Document that bodies should only wire steps. The plan never runs step functions.
- **Branching on a step's result content stops the plan early.** → `complete: false` and `blocked_by` are shown in the UI as "Plan stops at X — it depends on X's output".
- **The placeholder downstream gives an upper bound.** A re-run upstream step that produces byte-identical output still lets downstream steps reuse. → Word it as "will run unless the upstream output turns out the same". Never assert that the run equals the plan in that case.
- **The server's env can differ from the runner's.** → Show `env` in the plan's technical details. `DENDRITE_ENV` is the knob.
- **Reloading a module can fail on a syntax error mid-edit.** → Keep the old module and return the plan with `error: "reload failed: …"`.
- **The read guard is noisy for steps that legitimately read config files under the repo.** → It reports each path once per step, is configurable with `DENDRITE_CHECK_READS=off`, and the warning names the fix (pass the file as an artifact). An audit hook costs one check per `open`, which is negligible next to step work.
- **Plan latency on big pipelines.** Each step is one `find_success` query plus a verify. → Fine for tens of steps. Cache plans per (experiment, config hash, code mtimes) for the sidebar counts.

## Migration Plan

There is no schema migration. Old DBs just never contain `draft`. Rollback means deleting the draft rows (`status='draft'`), which have no node_runs.

## Open Questions

- Should `run()` on a `created` (not draft) experiment keep working unchanged? Proposed answer: yes, `created` stays a valid frozen-before-run state for the Python API.
- Should forking a running experiment be allowed? Proposed answer: yes. Its config is already frozen.
