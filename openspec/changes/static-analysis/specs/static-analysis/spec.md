## ADDED Requirements

### Requirement: Diagnostics
The system SHALL report problems as diagnostics with a stable `code`, a `severity` of `error` or `warning`, a message, a file, a line and column, and the step concerned. Analysis SHALL never execute the analysed source and SHALL never affect execution signatures.

#### Scenario: Diagnostic location
- **WHEN** a step's literal return doesn't match its declared outputs
- **THEN** the diagnostic has code `return-shape`, severity `error`, and the file and line of that `return`

### Requirement: Step checks when the decorator runs
When `@artifact_task` decorates a function, the system SHALL analyse it and emit each diagnostic as a `StepDefinitionWarning`. Decoration SHALL NOT fail because of a diagnostic. Codes listed in `ignore=` SHALL be suppressed for that step. The checks are:

| Code | Severity | Finds |
|---|---|---|
| `return-shape` | error | a literal tuple or dict return that doesn't match `outputs` |
| `no-return` | error | no value is ever returned |
| `bad-output-name` | error | an output name that isn't an identifier |
| `default-not-json` | error | a default that can't be JSON-encoded |
| `hidden-read` | warning | a read from a literal path |
| `hidden-env` | warning | reads of environment variables |
| `hidden-clock` | warning | reads of the clock |
| `unseeded-random` | warning | random calls with no `seed` param |

#### Scenario: Wrong tuple length
- **WHEN** a step declares `outputs=["cameras", "sparse"]` and contains `return cams`
- **THEN** a `StepDefinitionWarning` with code `return-shape` is emitted at import time, and the step is still registered

#### Scenario: Silenced heuristic
- **WHEN** a step decorated with `ignore=("hidden-env",)` reads `os.environ["X"]`
- **THEN** no `hidden-env` warning is emitted

#### Scenario: Source unavailable
- **WHEN** a step's source can't be retrieved
- **THEN** analysis is skipped without error

### Requirement: Static pipeline shape
`Pipeline.shape()` and `analysis.pipeline_shape(source, resolve)` SHALL return the pipeline's dataflow graph from source alone:
- nodes for inputs, step calls (including `.submit` and `with_options(name=...)`) and dynamic calls
- edges for values passed between them, labelled with the output name

Nodes SHALL carry their source line. Steps under `if`/`else` SHALL carry the condition text, steps in loops or comprehensions SHALL be marked `repeated`, and calls the parser can't resolve that receive pipeline values SHALL be `dynamic` nodes. A helper function defined in the same module SHALL be expanded one level.

#### Scenario: Conditional branch
- **WHEN** a pipeline body contains `if refine: train_fine(model)`
- **THEN** the shape has a `train_fine` node with `conditional == ["refine"]` and an edge from the producer of `model`

#### Scenario: Unsaved source
- **WHEN** the shape is requested for edited source text that hasn't been saved
- **THEN** names resolve against the loaded module, and nothing is executed

#### Scenario: Syntax error
- **WHEN** the source doesn't parse
- **THEN** the result has no nodes and one `syntax` diagnostic with its line and column

### Requirement: Pipeline body diagnostics
The shape SHALL include these diagnostics:

| Code | Finds |
|---|---|
| `unpack-mismatch` | the number of tuple targets differs from the step's outputs |
| `unknown-arg` | a keyword the step doesn't accept |
| `missing-arg` | a required parameter that isn't supplied |
| `duplicate-call` | the same node called twice where both calls can run in one execution |
| `call-in-loop` | a step called in a loop without a distinct name |
| `undefined-name` | an argument that refers to nothing |

Calls in mutually exclusive `if`/`else` arms SHALL NOT count as duplicates.

#### Scenario: Unpacking mismatch
- **WHEN** a body has `cameras = colmap(frames)` against `outputs=["cameras", "sparse"]`, or `a, b, c = colmap(frames)`
- **THEN** the first is accepted (a single target takes the whole tuple), and the second reports `unpack-mismatch`

#### Scenario: Exclusive branches
- **WHEN** `train` is called once in the `if` arm and once in the `else` arm
- **THEN** no `duplicate-call` is reported

### Requirement: Experiment config checks
`Experiment.check()` SHALL report params and pins whose step isn't in the pipeline's shape (`unknown-step`) and param names the step doesn't accept (`unknown-param`), before anything runs. When the shape contains dynamic nodes, `unknown-step` SHALL be a warning instead of an error.

#### Scenario: Typo in a param key
- **WHEN** a draft has params `{"trian": {"iterations": 5}}`
- **THEN** `check()` reports `unknown-step` for `trian`
