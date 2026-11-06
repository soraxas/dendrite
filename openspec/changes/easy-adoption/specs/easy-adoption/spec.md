## ADDED Requirements

### Requirement: Steps run outside a pipeline

Calling a step outside a running pipeline SHALL execute it immediately against the default lab (or the one set with `dendrite.use`), with the same signature, cache verification, telemetry and lineage as inside a pipeline, and SHALL return plain values: a `Path` for file or directory outputs, the object for value outputs, a tuple for several outputs. `Path` arguments (also nested in lists or dicts) SHALL become file or directory inputs, JSON values params, and other objects value inputs.

#### Scenario: One decorator

- **WHEN** existing code calls `extract(Path("clip.mp4"), fps=4)` on a function decorated with `@dendrite.step`, twice
- **THEN** both calls return an equal `Path`, and the function body ran once

#### Scenario: Chaining links lineage

- **WHEN** the `Path` one eager step returned is passed to another eager step
- **THEN** the second step's input is the first step's output artifact, and lineage connects them

#### Scenario: Object arguments

- **WHEN** an eager step is called with a non-JSON object, such as a set of numbers
- **THEN** the object is stored as a value input and an equal object later is a cache hit

### Requirement: Sessions record eager calls

Eager calls in a process SHALL be recorded in one session experiment per lab, named after the script and its start time, whose node runs, inputs, logs and lineage appear like any experiment's. Calling the same step again in a session SHALL record `step[2]`, `step[3]`, and so on. A session SHALL be `running` only while a step executes.

#### Scenario: Repeated calls

- **WHEN** a script calls the same eager step three times with different arguments
- **THEN** its session shows `step`, `step[2]` and `step[3]`

### Requirement: Wrapping functions you can't edit

`dendrite.step(fn)` SHALL return a step for any callable, including functions without Python source, whose code fingerprint then comes from the module, qualified name and distribution version.

#### Scenario: A builtin

- **WHEN** `dendrite.step(math.factorial)(20)` runs twice
- **THEN** the second call is a cache hit

### Requirement: Rerun anyway

`Experiment.run(rerun=...)` (a list of node names, or `True` for all), `step.rerun(...)` and `dendrite run EXP --rerun NODE` / `--rerun-all` SHALL recompute the named steps even when a verified result exists, publish the new result so later runs reuse it, and replace the experiment's node runs. When a rerun's outputs differ from the result it replaced, the system SHALL emit a `NondeterministicStep` warning and write a line saying so to the step's log.

#### Scenario: Rerun one step

- **WHEN** a completed experiment is run again with `rerun=["train"]`
- **THEN** `train` computes, everything else is reused, and the experiment completes

#### Scenario: Nondeterminism

- **WHEN** a step whose output depends on unseeded randomness is rerun
- **THEN** a `NondeterministicStep` warning names the step, and its log says the outputs differ from the earlier run

### Requirement: Cache modes

`DENDRITE_CACHE` SHALL accept `on` (default), `off` (recompute everything; record and publish), `readonly` (reuse existing results; record new ones as not reusable) and `bypass` (eager calls run the plain function and record nothing; pipelines behave as `off`).

#### Scenario: Off

- **WHEN** an experiment runs twice with `DENDRITE_CACHE=off`
- **THEN** every step computes both times

#### Scenario: Readonly

- **WHEN** a lab already has a step's result and another step is new, under `DENDRITE_CACHE=readonly`
- **THEN** the known step is reused, the new one computes, and running again computes the new one again

#### Scenario: Bypass

- **WHEN** an eager step is called with `DENDRITE_CACHE=bypass`
- **THEN** the function runs and nothing is written to the lab
