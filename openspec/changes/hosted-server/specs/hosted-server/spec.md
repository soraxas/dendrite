## ADDED Requirements

### Requirement: Self-hostable server

The system SHALL provide `dendrite server --config server.toml`. It serves the versioned API under `/api/v1`, the auth endpoints and the web UI.

The config SHALL name:

- the Postgres URL
- the blob store URL
- the public base URL
- the OIDC provider settings
- optionally, initial admin emails and auto-join domains

The database and the bucket SHALL be reachable by the server alone; clients SHALL NOT need credentials for either.

#### Scenario: Clients hold no infrastructure credentials

- **WHEN** a runner with only a server URL and a dendrite token runs a pipeline
- **THEN** its lineage is recorded and its outputs are stored, without the runner having database or bucket credentials

#### Scenario: Server requires Postgres

- **WHEN** the server config names a SQLite URL
- **THEN** the server refuses to start and explains that hosted mode needs Postgres

### Requirement: Projects isolate lineage, cache and blobs

Each project SHALL have its own metadata, stored in its own Postgres schema with the standard dendrite tables, and its own blob prefix.

Cache reuse, lineage queries, garbage-collection reachability and deletion SHALL stay within a project. Cache lookups SHALL go through a per-project list of cache scopes, which this change fixes to the project itself, so that cross-project cache grants can be added later without changing signatures or stored data.

#### Scenario: Reuse within a project

- **WHEN** user A runs step `colmap`, and later user B, in the same project, runs it with the same signature
- **THEN** B's node run is a cache hit on A's execution

#### Scenario: No reuse across projects

- **WHEN** the same signature was computed in project `recon`
- **THEN** a run in project `splats` computes it again, and cannot obtain URLs for `recon`'s blobs

### Requirement: Roles

Each project membership SHALL have one role:

- **`viewer`:** read metadata and download blobs
- **`runner`:** everything `viewer` can do, plus create and run experiments, upload blobs and delete experiments the user owns
- **`admin`:** everything `runner` can do, plus delete any experiment, set keep flags, mark runs stopped, evict, collect garbage, clean up, and manage memberships and tokens

The server SHALL reject a call that needs a higher role with 403.

#### Scenario: Viewer cannot run

- **WHEN** a viewer's client tries to create an experiment
- **THEN** the server answers 403, and nothing is written

#### Scenario: Runner deletes only their own experiments

- **WHEN** a runner deletes an experiment created by another user
- **THEN** the server answers 403
- **AND WHEN** the runner deletes one they created
- **THEN** it is deleted under the existing deletion rules

### Requirement: Remote-callable metadata operations

The server SHALL expose metadata operations only through an explicit allowlist. Each entry names the required role and, for writes, a check bound to the caller:

- **Node runs, stages, progress and pipeline reads:** the caller SHALL own the experiment.
- **Leases:** the lease holder SHALL be bound to the caller's token. Only the holder may renew or release a live lease. An expired lease SHALL remain claimable by anyone.
- **Publish:** the caller SHALL hold the lease for that signature, and every output blob SHALL be committed. It SHALL commit in one transaction, as locally.
- **Draft edits and the draft-to-running transition:** these SHALL remain compare-and-set operations.

An operation not in the allowlist SHALL answer 404.

#### Scenario: Publishing without the lease

- **WHEN** a runner publishes an execution for a signature whose lease another worker holds
- **THEN** the server answers 409, and nothing is committed

#### Scenario: Unlisted operation

- **WHEN** a client calls an internal `Metadata` method that isn't on the allowlist
- **THEN** the server answers 404

### Requirement: Storage administration runs on the server

Inventory, eviction, garbage collection and scratch clean-up SHALL execute on the server, against the project's store. The same safety rules as locally SHALL apply: the grace period, the busy refusal, and keeping pinned outputs. These operations SHALL require `admin`, except inventory, which `viewer` may read.

#### Scenario: GC from the CLI in hosted mode

- **WHEN** an admin runs `dendrite gc --yes` against a server project
- **THEN** the server collects that project's unreachable blobs and returns the report, and the client never lists the bucket

### Requirement: Run attribution

The server SHALL record the user, or the service token's name, that created each experiment and each execution. It SHALL store this in side tables, without changing the core tables. Experiment and execution responses SHALL include it.

#### Scenario: Who ran it

- **WHEN** user B forks user A's experiment and runs it
- **THEN** the fork is attributed to B, and reused executions remain attributed to A

### Requirement: Adopting an existing deployment

`dendrite server adopt --schema S --prefix URL --project NAME` SHALL register an existing direct-mode Postgres schema and blob prefix as a project, without copying or rewriting data.

#### Scenario: History carries over

- **WHEN** a schema with 40 experiments is adopted as project `recon`
- **THEN** all 40 experiments and their lineage appear in the project, and new runs reuse their executions

### Requirement: Version negotiation

Every client request SHALL carry `X-Dendrite-Version`. The server SHALL reject clients older than its configured minimum with 426 and an upgrade message. The `/api/v1` API SHALL change only additively.

#### Scenario: Outdated client

- **WHEN** a client older than `min_client` calls the API
- **THEN** it receives 426, and the CLI prints the required version
