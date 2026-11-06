## ADDED Requirements

### Requirement: Execution telemetry tabs

The node detail panel SHALL offer Console, Scalars, Samples and Environment tabs for the node run's execution. Console SHALL tail live while the execution runs, follow the end unless the user scrolls up, render ANSI colours, collapse carriage-return updates to their final state, search, and download the raw log. Scalars SHALL draw one chart per title with a line per series and a smoothing control. Samples SHALL show each title and series with a step slider defaulting to the latest step. Environment SHALL show git, diff, system and packages. When the execution was reused, each tab SHALL say which experiment ran it and link to it.

#### Scenario: Live console

- **WHEN** the user opens Console on a running step
- **THEN** new output appears within a few seconds without reloading

#### Scenario: Progress bar collapses

- **WHEN** a step's output contains a tqdm bar updated with carriage returns
- **THEN** the console shows one line with the bar's latest state

#### Scenario: Reused execution

- **WHEN** the user opens Scalars on a cached node
- **THEN** the charts come from the original execution and a note links to the experiment that ran it

### Requirement: Metrics page charts

The experiment Metrics page SHALL show scalar charts grouped by node and title, with the existing table available as a toggle.

#### Scenario: Experiment overview

- **WHEN** an experiment's `train` and `eval` steps logged metrics
- **THEN** the Metrics page shows each node's charts under its name
