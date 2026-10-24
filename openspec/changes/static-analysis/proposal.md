## Why

Mistakes in step and pipeline declarations show up too late today:

- A step's return shape is checked against its `outputs` only *after* it has computed, so a typo wastes the whole run.
- A param for a step the pipeline never calls produces only a warning, at the end of the run.
- Node and output names are bare strings with no help from tooling.

The editing UI can also only show a pipeline's shape by planning saved, importable code. Parsing the source (AST) needs neither. It can check declarations the moment a module loads, and it can draw the flow as you type, conditional branches and loops included.

## What Changes

- **Step checks when the decorator runs.** These come from parsing the step function's source:

  - literal `return` tuples or dicts that don't match the declared `outputs`
  - steps that never return a value
  - output names that aren't identifiers
  - defaults that can't be JSON-encoded
  - likely hidden inputs: literal-path reads, `os.environ`/`getenv`, clock reads, unseeded `random`

  Each is reported as a `StepDefinitionWarning` with file and line. Decoration never fails because of them.

- **Static pipeline shape.** `Pipeline.shape()` parses the pipeline body into a dataflow graph:

  - step calls become nodes, and variables passed between calls become edges
  - steps under an `if` are marked *conditional* (with the condition's source text), steps in a loop *repeated*, and calls the parser can't resolve *dynamic*

  It also reports:

  - tuple unpacking that doesn't match a step's outputs
  - unknown or missing arguments
  - a step called twice outside mutually exclusive branches, or inside a loop

  It works on unsaved source text too, without executing it.

- **Experiment checks.** `Experiment.check()` flags params and pins naming steps or args the pipeline never uses, before anything runs.

- **Typed declarations, so fewer bare strings.**

  - When `outputs=` is omitted, it is inferred from a `NamedTuple` or `TypedDict` return annotation.
  - `step.override(**params)` builds validated `{"step.arg": value}` overrides for `fork`, `update` and `create`.

- **API and CLI.**

  - `GET /api/pipelines/{name}/shape` returns the shape of the loaded code.
  - `POST /api/pipelines/{name}/shape` returns the shape and diagnostics for edited, unsaved source.
  - `GET /api/experiments/{id}/check` returns an experiment's config diagnostics.
  - `dendrite check [--import MODULE]` prints every diagnostic and exits non-zero on errors, for CI.

- **UI: a live pipeline editor.** The Pipeline view gains a "Code" mode:

  - the pipeline function's source sits in the existing Ace editor, with diagnostics as gutter markers
  - the shape graph beside it re-parses as you type (debounced): conditional steps dashed, loops badged, dynamic calls as a "can't see inside" box
  - for a draft, each shape node also carries its planned outcome once saved
  - saving uses the existing safe source-write path: hash-checked, recorded file only, editing enabled only

- **Non-goals:**

  - widening the code fingerprint to cover helper functions (changes cache keys; a follow-up)
  - typing overrides with `ParamSpec` for pyright (a follow-up)
  - expanding helper functions beyond one level

## Capabilities

### New Capabilities

- `static-analysis`: diagnostics for step declarations, pipeline bodies and experiment configs, plus the static pipeline shape.
- `pipeline-editor-ui`: the live Code mode, with the editor, diagnostics, shape graph, plan overlay and save.

### Modified Capabilities

- `prefect-integration`: adds requirements for outputs inferred from return annotations, `step.override()`, and checks when the decorator runs.
- `lineage-ui`: adds requirements for the shape and check endpoints and the `dendrite check` command.

## Impact

- **Code:**
  - `src/dendrite/analysis.py`, new: AST walkers and diagnostics
  - `task.py`: decoration-time checks, inferred outputs, `override`
  - `experiment.py`: `Pipeline.shape`, `Experiment.check`
  - `db.py`: `save_snapshot`, recording a pipeline's source so it can be edited
  - `server.py`, `cli.py`
  - `ui/src`: Code mode
- **Signatures:** unchanged. Analysis never feeds the cache key.
- **Risks:**
  - False positives in the heuristic hidden-input checks. They are warnings only, each with a code that can be silenced.
  - Code that is hard to parse: dynamic dispatch and decorators that wrap steps. These are shown as dynamic regions rather than guessed.
