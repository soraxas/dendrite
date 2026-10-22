## Why

The UI is a good read-only view of history, much like GitHub Actions' run graph. Starting a new experiment, though, still means writing `exp.fork(params={...})` in Python and only discovering what it costs after it runs. The most valuable thing dendrite knows is which steps a change will reuse, and the UI can't currently show that before anything runs. This is phase 1 of turning the UI into an editing frontend.

It keeps the GitHub Actions split:
- Python code defines the pipeline's steps and wiring.
- The UI supplies the inputs to it: params and frozen results.

## What Changes

- **Draft experiments.** A new `draft` status, the one mutable state an experiment can be in.
  - A draft's params, pins, inputs and name can be edited.
  - Running a draft freezes it, after which it behaves like any other experiment.
  - Python API:
    - `exp.fork(..., draft=True)`
    - `lab.create(..., draft=True)`
    - `draft.update(params=..., pin=..., inputs=..., name=...)`
    - `draft.discard()`
- **Run plan (dry run).**
  - `Experiment.plan()` runs the pipeline body in plan mode. Each step computes its execution signature and checks the cache, then stops without executing.
  - **Cache hits** return their real refs, so later steps hash exactly.
  - **Misses** return placeholder refs, so everything downstream is marked as will-run.
  - Each step gets one of these outcomes: `reuse` (with the source execution and experiment), `run` (with a reason and an estimated duration from previous executions), or `frozen`.
  - The plan also reports each step's editable params (from its function signature) and how complete the plan is.
  - It works for any experiment, not just drafts.
- **API.** Endpoints to create a draft by forking, edit a draft, discard a draft, and fetch any experiment's plan. Changes are gated by the same `allow_edit` switch as source editing. Planning needs the pipeline module to be imported (`dendrite ui --import <module>`).
- **CLI.** `dendrite run <experiment> --import <module>` runs (and so freezes) a draft or a created experiment. In phase 1, this is how a draft made in the UI gets run.
- **UI.**
  - A "Fork…" action creates a draft and opens it in draft mode, which has a distinct banner and background.
  - Nodes show their planned outcome instead of a run status: Reused, Will run, Will run because upstream changed, or Frozen.
  - The side panel edits a step's params, showing the inherited value and a reset control.
  - Freeze / unfreeze is a toggle on each step.
  - A summary line totals the plan, e.g. "Will run 2 of 4 steps · about 14 min".
  - The panel shows the exact command to run the draft.
  - Drafts appear in the sidebar marked as drafts.
- **The step contract and the read guard.**
  - The README and design document the contract: the pipeline body only wires steps; steps may have side effects but must be hermetic in what they declare; params are JSON values and need no type hints.
  - A best-effort audit-hook guard warns when a step reads a file under the project directory that it didn't declare. `DENDRITE_CHECK_READS=warn|error|off` controls it, with `warn` the default.
- **Non-goals.** Editing the pipeline's steps or wiring from the UI (code stays the source of truth). Starting execution from the UI (phase 2 adds a runner). Sweeps, swapping in another experiment's result, and artifact viewers.

## Capabilities

### New Capabilities
- `draft-experiments`: the mutable draft state, its edit/discard operations, and freezing on run.
- `run-plan`: a dry run of a pipeline that predicts each step's outcome, reason and cost without executing anything.
- `experiment-editor-ui`: draft mode in the SPA: forking, param editing, freeze toggles, and plan display.

### Modified Capabilities
- `experiment-lineage`: "Immutable experiment snapshot" now allows a pre-run draft state. Config is frozen when an experiment first runs, not when it is created.
- `prefect-integration`: steps gain the undeclared-read guard, and the step contract is stated as a requirement.
- `lineage-ui`: adds two requirements, draft and plan endpoints and a `dendrite run` CLI command. These are added as new requirements rather than edits, because the open `react-flow-ui` change already modifies the existing API requirement.

## Impact

- **Code:**
  - `src/dendrite/experiment.py`: draft lifecycle, `plan()`.
  - `src/dendrite/task.py`: plan-mode branch in `__call__`/`submit`.
  - `src/dendrite/runtime.py`: plan flag on `RunState`.
  - `src/dendrite/server.py`: new endpoints.
  - `src/dendrite/cli.py`: `run`.
  - `src/dendrite/guard.py` (new): the audit hook; `task._compute` sets its contextvar.
  - `README.md`: the step contract.
  - `ui/src`: draft mode, plan rendering, param forms.
- **DB:** no schema change. `draft` is a new value of `experiments.status`.
- **Invariants:** signatures are unchanged, so plans and runs agree by construction. Running a plan never writes node_runs, executions or blobs.
- **Risks:**
  - The plan runs the user's pipeline body, so side effects outside steps also happen at plan time.
  - Branching on the *contents* of a result that hasn't been computed yet can't be predicted. The plan reports that it is incomplete in that case.
