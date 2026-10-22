## ADDED Requirements

### Requirement: Immutable experiment snapshot
The system SHALL create experiments with a frozen pipeline name, params, input artifact hashes and pins.

#### Scenario: Create experiment
- **WHEN** a user creates an experiment with params and input paths
- **THEN** the inputs are ingested as artifacts, and the experiment records their hashes

### Requirement: Configuration fork
The system SHALL fork an experiment by copying its configuration, applying overrides (dotted keys or nested dicts), and recording `parent_id`.

#### Scenario: Fork with dotted override
- **WHEN** `exp.fork(params={"train.iterations": 60})` is run
- **THEN** only `train` executes, and the new experiment's `parent_id` is `exp.id`

### Requirement: Artifact fork by pinning
The system SHALL allow pinning a node's outputs, either to another experiment's outputs or to explicit artifact hashes. A pinned node MUST be skipped and reported as `pinned`.

#### Scenario: Pin upstream node
- **WHEN** `exp.fork(pin={"featurize": exp}, params={"featurize.scale": 99})` is run
- **THEN** `featurize` is not executed, and its outputs equal those of `exp`

### Requirement: Experiment diff
The system SHALL report the parameter differences between two experiments, along with per-node signature equality and status.

#### Scenario: Diff after fork
- **WHEN** diffing a parent with a fork that changed `train.iterations`
- **THEN** the diff lists `train.iterations` as changed, and marks `train` as having a different signature

### Requirement: Artifact provenance queries
The system SHALL return the ancestors and descendants of an artifact (the transitive producing and consuming executions) using recursive SQL.

#### Scenario: Ancestors of a model
- **WHEN** querying the ancestors of `train`'s output
- **THEN** the result includes the `featurize` and `extract` executions and the source input artifact
