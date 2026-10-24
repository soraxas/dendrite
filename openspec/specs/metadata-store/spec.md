# metadata-store Specification

## Purpose

TBD - created by archiving change add-artifact-lineage. Update Purpose after archive.

## Requirements

### Requirement: SQLAlchemy-backed metadata

The system SHALL persist experiments, executions, artifacts, execution inputs and outputs, node runs, metrics and leases through SQLAlchemy. It SHALL be configured by a database URL.

#### Scenario: Default SQLite

- **WHEN** no `DENDRITE_DB_URL` is set
- **THEN** a SQLite database is created at `$DENDRITE_HOME/lineage.db`, with WAL mode enabled

#### Scenario: PostgreSQL

- **WHEN** `DENDRITE_DB_URL` is a `postgresql+psycopg://` URL
- **THEN** the same schema is created in PostgreSQL, and every feature behaves identically

### Requirement: Separate experiment, node-run, execution and artifact entities

The system SHALL model these as distinct tables, so that one execution can be referenced by node runs of many experiments.

#### Scenario: Shared execution

- **WHEN** experiments A and B both resolve node `colmap` to the same signature
- **THEN** both have a node run for `colmap` that references the same execution id

### Requirement: Atomic publish

The system SHALL commit the following in one transaction:

- execution success
- output edges
- input edges
- metrics
- the node run
- the lease release

#### Scenario: Crash before publish

- **WHEN** a node raises after writing blobs but before publish
- **THEN** no success execution or cache entry exists for that signature
