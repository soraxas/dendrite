## Context

Two ways to build the graph now exist side by side:
- **Tracing:** `Experiment.plan()` runs the pipeline body with stand-in values. It is exact for one experiment, but needs saved, importable code.
- **Declaration checks:** these happen at runtime only (see the proposal).

Python's `ast` module parses any source text without running it. That is what both load-time checks and an as-you-type editor need. For reference, Metaflow builds its flow graph from the AST, and Dagster and Flyte build theirs by tracing, as our plan does.

## Goals / Non-Goals

**Goals:**
- Find declaration mistakes when the module loads, each with a file:line.
- Show a pipeline's shape from source alone, including source that hasn't been saved, with conditional, loop and dynamic regions marked honestly.
- Fewer bare strings for outputs and param overrides.
- Reuse the existing safe source-write path for saving edits.

**Non-Goals:**
- Executing unsaved code.
- Changing signatures.
- Pyright-level typing.
- Expanding helpers transitively.

## Decisions

### D1. Diagnostics are data, warnings only when the decorator runs
`analysis.Diagnostic(code, severity, message, file, line, col, step)` is the single record type. The codes are stable kebab-case names, listed in the specs.
- **Severity:** `error` means certainly wrong when reached, for example a literal return that doesn't match the declared outputs. `warning` means likely wrong.
- **At decoration:** diagnostics are emitted as `StepDefinitionWarning`, never raised. A raise would break import over a branch that may never run, which a CI check handles better.
- **Silencing:** `@artifact_task(ignore=("hidden-read",))` silences codes for one step.
- **`dendrite check`:** exits 1 on any error.

*Alternative:* raise `TypeError` at decoration for errors. Rejected, because an unreachable branch would stop the module importing.

### D2. Step analysis is local and conservative
The step analysis parses `textwrap.dedent(inspect.getsource(fn))`. If the source is unavailable, it skips. It walks only the function's own body, not nested functions or lambdas.

**Checks on returns:**
- **`return-shape`:** a literal `Tuple` whose length differs from `outputs`, a literal `Dict` with constant keys that differ from `outputs`, or a bare value returned when there are several outputs.
- **`no-return`:** no `return <value>` anywhere in the body.

Non-literal returns are not judged.

**Checks done at decoration rather than by parsing:**
- **`bad-output-name`:** `str.isidentifier()` on each output.
- **`default-not-json`:** `canonical_json(default)` on each default.

**Hidden-input heuristics** (warnings):

| Code | Pattern |
|---|---|
| `hidden-read` | `open("<literal>")`, or `Path("<literal>")` followed by `.read_text/.read_bytes/.open` |
| `hidden-env` | `os.environ[...]`, `os.environ.get`, `os.getenv` |
| `hidden-clock` | `time.time/monotonic/perf_counter`, `datetime.now/utcnow/today` |
| `unseeded-random` | a `random.<fn>` or `np.random.<fn>` call, when no param is named `seed`/`*_seed` |

The matching is by name, which is cheap and predictable, with no import resolution.

### D3. The pipeline shape is a def-use graph over the body
`analysis.pipeline_shape(source, resolve)` takes the pipeline's source text and a resolver. The resolver maps a name to an `ArtifactTask` or `None`, and is built from the pipeline function's `__globals__`, looked up lazily. Lazy matters because steps defined below the pipeline don't exist yet when its decorator runs.

**Walk.** The analyser walks statements in order, keeping:
- `env: dict[str, Value]`, where a `Value` is an input (a pipeline parameter), a step output, or unknown
- a context stack of `if <cond>` / `else` / `loop` / `with` / `try` frames

**Nodes.** A call becomes a step node when its function resolves:
- `step(...)`, `step.submit(...)`, and `step.with_options(name="x")(...)` (the node name comes from a literal `name=`)
- `fut.result()` passes its value through unchanged
- **Arguments:** positional and keyword arguments bind to the step's signature. Each argument holding a `Value` adds an edge from its producer, labelled with the output name.
- **Assignments:** `a, b = step(...)` maps outputs by position, and a single target takes the tuple. `x = other` copies the value.

**Context.**
- **Conditional:** a node under `if` carries `conditional: [<condition source>, …]`, with `else` frames shown as `not (<cond>)`.
- **Repeated:** inside `for`, `while`, or a comprehension, the node is `repeated: true`.

**Calls it can't resolve.**
- **Dynamic:** a call whose function doesn't resolve but receives a `Value` becomes a `dynamic` node with the call's source text. Its result is unknown.
- **One-level helpers:** a helper defined in the same module is expanded one level, by parsing its source and binding its parameters. Anything deeper is `dynamic`.

**Diagnostics:**

| Code | Meaning |
|---|---|
| `unpack-mismatch` | tuple targets ≠ the step's output count |
| `unknown-arg` | an argument name the step doesn't accept |
| `missing-arg` | a required parameter not supplied and without a default |
| `duplicate-call` | the same node name called twice where both calls can run in one execution (not in mutually exclusive `if`/`else` arms) |
| `call-in-loop` | a step called inside a loop without `with_options(name=…)` |
| `undefined-name` | an argument name that is neither a pipeline input, an earlier assignment, nor a module global |

**Output.** Nodes, edges and diagnostics, in the same node and edge shape as the DAG. Each node has `kind: "node" | "input" | "dynamic"`, `line`, `conditional`, `repeated` and `outputs`. That lets `Graph` render it with the existing ELK layout.

*Alternative:* trace with stand-in values, as plan does, with branches forced both ways. Rejected, because it needs execution and can't run on unsaved text.

### D4. Saving the pipeline's source goes through `code_snapshots`
`Pipeline.source_snapshot()` reuses `task.source_snapshot(fn)`. `db.save_snapshot(snap)` inserts it once, keyed by its hash.

`GET /api/pipelines/{name}/shape` returns the snapshot hash, so the UI can call the existing `GET`/`PUT /api/sources/{hash}`, which bring:
- the disk-status check
- the file hash check
- the recorded-path-only rule
- the `allow_edit` gate

There's no new write path. After a save, the plan's module reload (from draft-experiments) picks the change up.

### D5. Analysing unsaved text
`POST /api/pipelines/{name}/shape {source}` parses the given text.
- **Names** resolve against the *loaded* module's globals, so steps are known even though the text isn't saved.
- **Syntax errors** become one `syntax` diagnostic with its line and column, and an empty graph.
- **Safety:** the endpoint never executes the text and is read-only. It is allowed without `allow_edit`.

### D6. Outputs inferred from the return annotation
When `outputs=` is omitted and `typing.get_type_hints(fn)["return"]` is a `NamedTuple` subclass, outputs come from its `_fields`. For a `TypedDict`, they come from `__annotations__` in order.
- **Resolution timing:** annotations are resolved when the decorator runs. If that fails (for example a forward reference), it falls back to `("output",)` with a `StepDefinitionWarning` (`unresolved-return-type`).
- **Returns:** a returned `NamedTuple` instance normalises like a tuple; a `TypedDict` is a dict.

### D7. `step.override(**params) -> dict[str, Any]`
It validates each key against the step's parameters: an unknown key raises `TypeError`, with a close-match suggestion from `difflib`. It can't reject artifact inputs, because a parameter only becomes one when an `ArtifactRef` is passed to it; `_split` still rejects that at execution.

It returns `{"<step>.<arg>": value}`. Because the result is a plain dict, it composes: `fork({**colmap.override(matcher="x"), **train.override(lr=1e-3)})`.

### D8. `Experiment.check()` compares config against the shape
- **`unknown-step`:** a key in `params` or `pins` names no node in the static shape.
- **`unknown-param`:** a key names a param that node's signature lacks.
- **When the shape has dynamic regions:** an `unknown-step` might really be a step hidden in one, so it is downgraded to a warning.

`run()` keeps its end-of-run warning for steps the pipeline never called.

### D9. UI: Code mode
- **Entry point:** a "Code" tab beside "Pipeline" (`#/exp/:id/code`).
- **Layout:** in Code mode, the Ace editor (the existing `AceEditor` component) sits on the left and a `Graph` of the static shape on the right.
- **Re-parsing:** typing triggers a debounced `POST …/shape` (400ms). Diagnostics become Ace annotations; a click selects the node.
- **Graph:** shape nodes use the pipeline-node component:
  - conditional nodes are dashed and carry an `if …` chip
  - repeated nodes carry a `⟳` badge
  - dynamic nodes are a grey box showing the call's source text
- **Draft overlay:** for a draft, node tones come from the current plan by node name, so the graph reads like the Graph view.
- **Saving:** Save goes through `api.saveSource`, with the same confirm-and-conflict flow as `CodeModal` and the same unsaved-changes guard.

## Risks / Trade-offs

- **Heuristic hidden-input warnings can be noisy.** → They are warnings only, silenced per step by code, and `dendrite check --ignore CODE` silences them globally.
- **The static shape can disagree with the traced plan,** for example under dynamic dispatch. → The UI labels the shape "from the source" and the plan "for this experiment". Unresolved regions are drawn as such, never guessed.
- **Source is unavailable** for steps defined in a REPL or by `exec`. → Analysis is skipped, never an error.
- **Parsing cost** at every decoration is small (one parse per function), and it is cached per source hash.

## Migration Plan

Additive. Existing steps gain warnings at most, and no signatures change.

## Open Questions

- Should `dendrite check` run the plan for each experiment as well? Proposed: not by default; `--plan` opts in.
