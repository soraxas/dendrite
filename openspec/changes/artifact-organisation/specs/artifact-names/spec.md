## ADDED Requirements

### Requirement: Names label artifacts without changing them

The system SHALL let a user attach a human-readable name to an artifact, with an optional note. A name SHALL match `[A-Za-z0-9][A-Za-z0-9._-]{0,99}` and be unique per lab (per project in hosted mode). A name SHALL record its target hash and, when it was named from a run, the experiment id, node and output it came from, plus who set it and when. Setting, re-pointing or removing a name MUST NOT change any artifact, blob, execution, node run or signature.

#### Scenario: Name an output

- **WHEN** a user names `exp42:train.model` as `best-model`
- **THEN** `best-model` resolves to that node run's `model` hash and records `exp42`, `train` and `model` as its source

#### Scenario: Names never affect cache keys

- **WHEN** an experiment runs before and after an artifact it uses is named
- **THEN** every execution signature is identical and the second run reuses every result

#### Scenario: Invalid or duplicate name

- **WHEN** a user sets a name containing `/` or whitespace, or a name that already points elsewhere without asking to move it
- **THEN** the request is rejected and nothing changes

### Requirement: Re-pointing keeps history

Moving an existing name to a different artifact SHALL require an explicit move (`--force`, `move=true`). The previous target SHALL be appended to that name's history with when it was replaced and by whom. Removing a name SHALL also record the removal in its history. Setting a name to the target it already has SHALL only update its note.

#### Scenario: Move a name

- **WHEN** `best-splat` points at hash A and the user moves it to hash B
- **THEN** `best-splat` resolves to B, and its history lists A with the time and user that replaced it

#### Scenario: Remove a name

- **WHEN** the user removes `best-splat`
- **THEN** `@best-splat` no longer resolves, the artifact stays in the store, and the history still shows its past targets

### Requirement: @name resolves wherever an artifact is accepted

`Lab.artifact`, `Lab.load`, experiment `inputs` and pins SHALL accept `@<name>` and resolve it to the name's current hash. Experiment config SHALL store the resolved hash, never the name, so a later move does not change a frozen experiment. An unknown name SHALL raise a clear error naming it.

#### Scenario: Load by name

- **WHEN** code calls `lab.load("@best-splat")`
- **THEN** it returns the same value or path as loading the name's target hash

#### Scenario: Pin by name

- **WHEN** an experiment is forked with `pin={"colmap.cameras": "@good-cams"}` and `good-cams` is later moved
- **THEN** the fork's pin still holds the hash `good-cams` had when the fork was created

#### Scenario: Input by name

- **WHEN** an experiment is created with `inputs={"images": "@raw-scan"}`
- **THEN** its `images` input is the hash `raw-scan` points at, and no file path named `@raw-scan` is read

### Requirement: Names CLI and API

The CLI SHALL provide `dendrite name <name> <target> [--note TEXT] [--force]`, `dendrite names [--history NAME]` and `dendrite unname <name>`. A target SHALL be `<exp>:<node>.<out>`, `<exp>:<input>`, or a hash or unique hash prefix. The server SHALL provide `GET /api/names`, `PUT /api/names/{name}`, `DELETE /api/names/{name}` and `GET /api/names/{name}/history`. Mutating endpoints SHALL require editing to be enabled. Artifact, DAG and lineage responses SHALL include the names currently pointing at the hashes they mention, as a map from hash to names.

#### Scenario: Name from the CLI

- **WHEN** a user runs `dendrite name paper-fig3 exp7:render.video --note "camera ready"`
- **THEN** `dendrite names` lists `paper-fig3` with its target, kind, size, source and note

#### Scenario: Names on the DAG

- **WHEN** the DAG of an experiment is fetched and one of its outputs is named
- **THEN** the response's names map lists the name under that output's hash

#### Scenario: Editing disabled

- **WHEN** the UI server runs without editing and a client sends `PUT /api/names/x`
- **THEN** it answers 403 and nothing changes

### Requirement: Names in hosted mode and push

In hosted mode names SHALL be per project and attributed to the caller. A `runner` SHALL be able to create names and to move or remove names they set; moving or removing another user's name SHALL require `admin`. A name SHALL only point at an artifact the project already has. `dendrite push` SHALL carry names with the bundle and SHALL skip, and report, a name that already exists on the server with a different target.

#### Scenario: Runner moves someone else's name

- **WHEN** a runner tries to move a name another user set
- **THEN** the server refuses with a permission error

#### Scenario: Push with a conflicting name

- **WHEN** a local lab pushes `best` → A to a server where `best` → B
- **THEN** the server keeps `best` → B and the push reports the skipped name
