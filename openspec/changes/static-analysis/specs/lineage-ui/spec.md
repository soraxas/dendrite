## ADDED Requirements

### Requirement: Shape and check endpoints

The HTTP API SHALL provide these endpoints:

- `GET /api/pipelines/{name}/shape` returns the shape and diagnostics of the loaded pipeline, plus its source snapshot hash for viewing and editing through `/api/sources/{hash}`.
- `POST /api/pipelines/{name}/shape` takes `{source}` and returns the shape and diagnostics for that text, without executing it.
- `GET /api/experiments/{id}/check` returns config diagnostics.

An unknown pipeline SHALL answer 404 with a hint to `--import` it.

#### Scenario: Analyse edited text

- **WHEN** a client posts modified pipeline source
- **THEN** the response has nodes, edges and diagnostics for that text, and the file on disk is unchanged

### Requirement: Check command

The CLI SHALL provide `dendrite check [--import MODULE] [--ignore CODE]...`. It prints every step, pipeline and experiment diagnostic as `file:line: severity code: message`, and exits 1 if any error remains.

#### Scenario: CI gate

- **WHEN** a step's literal return doesn't match its outputs and `dendrite check --import pkg.pipelines` is run
- **THEN** the diagnostic is printed and the exit status is 1
