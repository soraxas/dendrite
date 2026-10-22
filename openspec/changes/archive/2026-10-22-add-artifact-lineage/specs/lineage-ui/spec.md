## ADDED Requirements

### Requirement: Lineage HTTP API
The system SHALL expose JSON endpoints for:
- the experiment list
- experiment detail (params, node runs, metrics)
- the experiment DAG (nodes and edges with status)
- the fork tree
- the experiment diff
- artifact detail and lineage
- execution detail

#### Scenario: DAG endpoint
- **WHEN** `GET /api/experiments/{id}/dag` is requested
- **THEN** the response contains node objects (name, status, signature, execution, outputs, duration) and edges labelled with artifact names

### Requirement: Live single-page app
The system SHALL serve a single-page app at `/`. The app SHALL:
- list experiments as a fork tree
- render the selected experiment's DAG, colour-coded by status
- refresh while the experiment is running
- show node details, outputs and metrics on selection

#### Scenario: Live progress
- **WHEN** an experiment is running and the UI is open
- **THEN** nodes transition visibly from queued to running to computed or cached, without a page reload

### Requirement: Output and lineage tracing
The system SHALL let a user open any artifact and see its producing execution, its upstream ancestry, its downstream consumers, and the experiments that reference it.

#### Scenario: Trace artifact
- **WHEN** a user clicks an output artifact
- **THEN** the UI shows its ancestors and descendants, and links them to their executions

### Requirement: CLI
The system SHALL provide a `dendrite` CLI with `list`, `show`, `diff`, `dag` (Mermaid), `tree`, `lineage` and `ui` commands.

#### Scenario: Launch UI
- **WHEN** `dendrite ui --port 8454` is run
- **THEN** the API and SPA are served on that port against the configured DB and store
