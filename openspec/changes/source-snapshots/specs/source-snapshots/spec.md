## ADDED Requirements

### Requirement: Source snapshot per execution

The system SHALL record, for every execution, the source text, file path, first line, module and qualified name of the node function. Records SHALL be de-duplicated by source hash, and MUST NOT affect execution signatures.

#### Scenario: Snapshot recorded

- **WHEN** a step executes
- **THEN** `GET /api/executions/{id}` includes `source` with the function's file, its line and its qualified name
- **AND** `GET /api/sources/{hash}` returns its exact source text

#### Scenario: Cache keys unchanged

- **WHEN** the same experiment is run again after this change
- **THEN** every step is reused, exactly as before

### Requirement: On-disk status of a snapshot

The system SHALL report, for a snapshot:

- `same` when the snapshot text occurs exactly once in the recorded file
- `missing` when the file does not exist
- `changed` otherwise

It SHALL also report the file's current hash.

#### Scenario: File edited after the run

- **WHEN** the function's file has been modified so that the snapshot text no longer appears
- **THEN** the status is `changed`, and the UI shows the snapshot read-only with an explanation

### Requirement: Safe source editing

The system SHALL accept edited source for a snapshot whose status is `same`. It SHALL:

- replace only the snapshot text in the recorded file, atomically
- write only to the recorded path
- refuse with 409 when the file's hash differs from the one the client loaded
- refuse with 403 when editing is disabled

#### Scenario: Save edited code

- **WHEN** a user edits a step's code, saves, and confirms the dialog
- **THEN** the file contains the new function text in place of the old, and the rest of the file is unchanged

#### Scenario: Stale editor

- **WHEN** the file changed on disk after the editor was opened
- **THEN** saving returns 409, and the UI asks the user to reload

#### Scenario: Editing disabled

- **WHEN** the server was started without editing enabled
- **THEN** `PUT /api/sources/{hash}` returns 403, and the UI offers view-only

### Requirement: Unsaved changes are never silently lost

The UI SHALL ask for confirmation before closing the editor with unsaved changes.

#### Scenario: Close with edits

- **WHEN** a user closes the editor after typing
- **THEN** a prompt asks whether to save or discard
