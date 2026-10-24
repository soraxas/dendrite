## ADDED Requirements

### Requirement: Draft and plan endpoints

The HTTP API SHALL provide these endpoints:

- `POST /api/experiments/{id}/fork` creates a draft.
- `PATCH /api/experiments/{id}` edits a draft's params, pins, inputs or name. `reset` returns `node.arg` keys to the parent's value, and `floats` names keys whose whole-number values are stored as floats. It answers 409 if the experiment isn't a draft.
- `DELETE /api/experiments/{id}` discards a draft. It answers 409 if the experiment isn't a draft or has children.
- `GET /api/experiments/{id}/plan` returns the plan in the DAG's node and edge shape. It answers 409 with `code: "pipeline_not_imported"` when the pipeline module isn't loaded.

The endpoints that change something SHALL answer 403 unless editing is enabled. `/api/info` SHALL report whether planning is available. Param values SHALL be accepted and returned as typed JSON.

#### Scenario: Editing disabled

- **WHEN** the server runs with editing disabled and a client sends `PATCH /api/experiments/{id}`
- **THEN** the response is 403 and the draft is unchanged

#### Scenario: Pipeline not imported

- **WHEN** `/plan` is requested for an experiment whose pipeline isn't registered
- **THEN** the response is 409 with `code: "pipeline_not_imported"` and a hint naming `--import`

### Requirement: Run command

The CLI SHALL provide `dendrite run <experiment> [--import MODULE]`, which runs a `draft` or `created` experiment in-process and prints its outcome.

#### Scenario: Run a draft made in the UI

- **WHEN** `dendrite run <draft-id> --import examples.recon` is run
- **THEN** the draft freezes and runs, and the UI shows it live like any other run
