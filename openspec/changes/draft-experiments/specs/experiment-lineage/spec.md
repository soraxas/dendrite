## MODIFIED Requirements

### Requirement: Immutable experiment snapshot
The system SHALL create experiments with a pipeline name, params, input artifact hashes and pins.
- A config SHALL be frozen from the moment the experiment first runs.
- An experiment created as a `draft` MAY be edited until then. Every other experiment is frozen at creation.
- Re-running only replaces node_runs.

#### Scenario: Create experiment
- **WHEN** a user creates an experiment with params and input paths
- **THEN** the inputs are ingested as artifacts, and the experiment records their hashes

#### Scenario: Draft freezes on first run
- **WHEN** a draft is run
- **THEN** its status leaves `draft`, and its params, inputs and pins can no longer change
