## ADDED Requirements

### Requirement: Dry-run plan
`Experiment.plan()` SHALL predict each step's outcome by executing the pipeline body with steps that stop after the cache check. It SHALL compute signatures with the same `spec` and `sha256_json` as real runs.

#### Scenario: Unchanged fork plans all reuse
- **WHEN** an experiment has completed and a fork with identical config is planned
- **THEN** every step's plan is `reuse`, with `reuse_from` naming the source execution and experiment

#### Scenario: Param change
- **WHEN** a draft changes `train_splats.iterations` and is planned
- **THEN** `train_splats` is `run` with reason `params`, its upstream steps are `reuse`, and the steps downstream of it are `run` with reason `upstream`

### Requirement: Plan has no side effects in dendrite state
Planning SHALL NOT write node_runs, executions, execution edges, metrics, leases, code snapshots or blobs. It SHALL NOT create Prefect flow or task runs, and it SHALL NOT invoke step functions.

#### Scenario: Table counts unchanged
- **WHEN** `plan()` is called on any experiment, including one whose pipeline uses `.submit()`
- **THEN** every dendrite table has the same row count before and after, and no step function was called

### Requirement: Outcomes, reasons and estimates
Each planned step SHALL report:
- `plan`: one of `reuse`, `run` or `frozen`
- for `run`, a `reason`: one of `code`, `params`, `inputs`, `env`, `upstream`, `new`, `missing_blob` or `uncached` (the step is declared `cache=False`)
- an `estimate`, the duration of the most relevant previous successful execution of the same op, or null
- a `param_schema` listing the step's editable params with their effective and inherited values

A step whose inputs come from a step marked `run` SHALL be reported as running unless the upstream output turns out unchanged. Its reason is `upstream`.

#### Scenario: Pinned step
- **WHEN** a step is pinned
- **THEN** its plan is `frozen`, and the steps downstream are planned against the pinned hashes

#### Scenario: Missing blob
- **WHEN** a matching successful execution exists but one of its blobs is missing from the store
- **THEN** the step is `run` with reason `missing_blob`

### Requirement: Incomplete plans
If the pipeline body reads the contents of a result that hasn't been computed (a placeholder), the plan SHALL stop and return the outcomes so far, with `complete: false` and `blocked_by` naming the step. Any other exception in the body SHALL be returned as `error`, and not raised.

#### Scenario: Branch on content
- **WHEN** the body calls `lab.load()` on the output of a step planned as `run`
- **THEN** the plan returns `complete: false` and `blocked_by` names that step

### Requirement: Fresh code in long-lived processes
Before planning, the system SHALL reload any imported pipeline module whose source file changed since it was imported, so code fingerprints match what a new run would compute. If the reload fails, the plan SHALL report an error and keep the previous module.

#### Scenario: Step edited on disk
- **WHEN** a step's source file is edited after the server imported it, and a fork that reused that step is planned again
- **THEN** that step is `run` with reason `code`

### Requirement: Exact param round-trip
Re-applying every param of a draft with its current value SHALL leave every signature unchanged.

#### Scenario: Re-save unchanged
- **WHEN** every param in a fully reused draft's `param_schema` is sent back through the API unchanged
- **THEN** the plan is still all `reuse`
