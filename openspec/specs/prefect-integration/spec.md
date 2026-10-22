# prefect-integration Specification

## Purpose
TBD - created by archiving change add-artifact-lineage. Update Purpose after archive.
## Requirements
### Requirement: Artifact task decorator
The system SHALL provide `@artifact_task(outputs=..., version=..., cache=..., packages=..., hash_source=...)`, which wraps a function as a Prefect task with `NO_CACHE`. The task SHALL accept and return only `ArtifactRef` objects at Prefect boundaries.

#### Scenario: Single and multiple outputs
- **WHEN** a node declares one output
- **THEN** it returns an `ArtifactRef`
- **WHEN** a node declares several outputs
- **THEN** it returns a tuple of refs in declared order

#### Scenario: Submit for parallel execution
- **WHEN** a pipeline calls `node.submit(...)` and passes the future to another node
- **THEN** Prefect resolves the future, and lineage is recorded identically

### Requirement: Pipeline decorator
The system SHALL provide `@pipeline`, which wraps a function as a Prefect flow. Its arguments are the experiment's input artifacts, and its flow-run name is the experiment id.

#### Scenario: Run creates Prefect flow run
- **WHEN** an experiment is run
- **THEN** a Prefect flow run is created, and its id is stored on the experiment

### Requirement: Node runtime helpers
The system SHALL provide `workdir()`, a scratch directory per execution, and `log_metric(key, value, step)`, which records metrics on the execution.

#### Scenario: Metrics shared by cache hits
- **WHEN** a cached node is reused by another experiment
- **THEN** that experiment reports the metrics of the original execution

