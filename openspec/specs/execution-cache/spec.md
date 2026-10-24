# execution-cache Specification

## Purpose

TBD - created by archiving change add-artifact-lineage. Update Purpose after archive.

## Requirements

### Requirement: Deterministic execution signature

The system SHALL compute a signature from:

- the node name and version
- the code fingerprint
- the environment fingerprint
- the content hashes of its input artifacts
- only the parameters the node consumes

The signature MUST NOT include experiment or flow-run identifiers.

#### Scenario: Unrelated parameter change

- **WHEN** only `train.iterations` changes between two experiments
- **THEN** the signatures of `extract` and `featurize` are identical across them

#### Scenario: Source change invalidates

- **WHEN** a node function's source code changes and `hash_source` is enabled
- **THEN** its signature changes

### Requirement: Global cache reuse

The system SHALL reuse the outputs of a previous successful execution with the same signature, from any experiment, provided that all of its output blobs verify.

#### Scenario: Identical experiment

- **WHEN** a new experiment has the same params and inputs as a completed one
- **THEN** every node is reported as `cached`, and no node function is invoked

#### Scenario: Missing blob forces recompute

- **WHEN** a cached execution's output blob is missing from the store
- **THEN** the node is executed again, and a new execution is published

### Requirement: Upstream invalidation propagates downstream

The system SHALL re-execute a node whose inputs changed hash, and therefore every descendant whose inputs change as a result.

#### Scenario: Upstream param change

- **WHEN** `featurize.scale` changes
- **THEN** `featurize` and `train` execute, and `extract` is cached

### Requirement: Lease-based de-duplication

The system SHALL ensure that at most one execution of a cacheable signature runs at a time. Concurrent requesters wait for, and then reuse, its result.

#### Scenario: Concurrent forks

- **WHEN** two experiments with the same signature for a node run concurrently
- **THEN** the node function executes exactly once, and both experiments reference the same execution

#### Scenario: Expired lease is stolen

- **WHEN** a lease holder stops heartbeating and the lease expires
- **THEN** another requester acquires the lease and executes

### Requirement: Declared non-cacheable nodes

The system SHALL always execute nodes declared with `cache=False` while still recording their lineage.

#### Scenario: Non-cacheable node

- **WHEN** a `cache=False` node is run twice with identical inputs
- **THEN** it executes twice, and two executions are recorded
