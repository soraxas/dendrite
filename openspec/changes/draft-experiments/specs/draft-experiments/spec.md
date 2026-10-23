## ADDED Requirements

### Requirement: Draft experiments
The system SHALL support experiments with status `draft`.
- A draft is created by `Lab.create(..., draft=True)` or `Experiment.fork(..., draft=True)`.
- It records `parent_id` and copies the parent's config like any other fork.
- It has no node_runs.

#### Scenario: Fork into a draft
- **WHEN** `exp.fork(draft=True)` is called
- **THEN** a new experiment exists with `status == "draft"`, `parent_id == exp.id` and the same params, inputs and pins

### Requirement: Editing a draft
`Experiment.update(params=..., pin=..., inputs=..., name=...)` SHALL apply overrides to a draft. It uses the same override semantics as `fork`:
- dotted keys
- `pin={"node": None}` to unfreeze a step
- `inputs` paths or refs

The update SHALL be a compare-and-set on `status = 'draft'`, and SHALL raise `NotADraft` for any other status.

#### Scenario: Edit a draft param
- **WHEN** `draft.update(params={"train.iterations": 60000})` is called
- **THEN** the draft's params reflect the change, and its parent is unchanged

#### Scenario: Edit a frozen experiment
- **WHEN** `update` is called on a `created`, `running`, `completed` or `failed` experiment
- **THEN** `NotADraft` is raised and nothing changes

### Requirement: Discarding a draft
`Experiment.discard()` SHALL delete a draft. It SHALL refuse experiments that aren't drafts, and drafts that have child experiments.

#### Scenario: Discard a draft
- **WHEN** a draft without children is discarded
- **THEN** it no longer appears in `lab.experiments()` or the fork tree

### Requirement: Running freezes a draft
`Experiment.run()` SHALL move a `draft` or `created` experiment to `running` with a compare-and-set on its current status, so a racing edit or a second runner fails. After that, the config can no longer be edited.

#### Scenario: Edit races run
- **WHEN** `run()` has moved a draft to `running` and `update` is called afterwards
- **THEN** `update` raises `NotADraft`, and the run uses the config as it was at the transition
