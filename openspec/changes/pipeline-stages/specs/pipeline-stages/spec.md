## ADDED Requirements

### Requirement: Declaring stages

`dendrite.stage(name, description=None, color=None)` SHALL work as a decorator and as a context manager. Every step called within it SHALL belong to that stage, stages SHALL nest, and `@artifact_task(stage=...)` SHALL apply only to steps called outside any stage. Stages SHALL NOT affect signatures.

#### Scenario: A function is a stage

- **WHEN** a pipeline calls a function decorated `@stage("Privacy")` that calls three steps
- **THEN** those three nodes report the stage path `["Privacy"]`, and their signatures equal those of the same steps called without the stage

### Requirement: Stages before a run

The static pipeline shape SHALL report the stage path of steps called inside `with stage(...)` blocks and `@stage` helper functions.

#### Scenario: Draft overview

- **WHEN** a draft's pipeline uses stages
- **THEN** its shape and plan nodes carry the stage paths, so the UI can show the overview before running

### Requirement: Stage overview

The pipeline graph SHALL show each stage as one card with its name, description, colour, combined status, progress and step count, with edges between stages merged. Stages SHALL start collapsed, each SHALL expand in place, and a switch SHALL show all steps.

#### Scenario: High-level picture

- **WHEN** a user opens an experiment whose pipeline has five stages
- **THEN** the graph shows five stage cards connected by their data flow, and expanding one shows its steps

### Requirement: Managing stages in the UI

A per-pipeline view SHALL let users assign ungrouped steps to stages and relabel, describe or recolour stages, without changing code or signatures. Code-defined stages SHALL take precedence. The UI SHALL offer the view as code to paste. Saving the view SHALL require editing to be enabled.

#### Scenario: Group someone else's pipeline

- **WHEN** a user assigns two ungrouped steps to a new stage "Audio" in the UI
- **THEN** every experiment of that pipeline shows an "Audio" stage card containing them, and nothing is recomputed

### Requirement: Sub flows and large fan-outs

An expanded stage or fan-out SHALL be drawn as a frame containing its steps. A fan-out SHALL never draw more than a small fixed number of items. Its card SHALL show a status grid of every item, batched when there are many. Expanding it SHALL draw failed, running and queued items first, plus a "+N more" node that opens the full, filterable list. Edges of a fan-out's items SHALL attach to its frame.

#### Scenario: A thousand frames

- **WHEN** a run calls `extract.named(i)` for 1000 frames
- **THEN** the graph draws one card with a status grid; expanding it draws at most 12 items, the rest are counted in "+988 more", and the full list is in the panel
