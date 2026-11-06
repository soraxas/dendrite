## ADDED Requirements

### Requirement: Experiments table

The UI SHALL list experiments in a table with default columns (name, status, pipeline, parent, created, duration, size, tags) and SHALL let the user add any parameter or any metric as its last, min or max value. Columns SHALL sort, and rows SHALL filter by text, status, pipeline, tag and parameter or metric range. The chosen columns SHALL persist per pipeline in the browser and appear in the URL.

#### Scenario: Best PSNR

- **WHEN** the user adds `evaluate.psnr (last)` and sorts it descending
- **THEN** the experiment with the highest last PSNR is first

#### Scenario: Share a view

- **WHEN** the user copies the URL of a table with chosen columns and a filter
- **THEN** opening it shows the same columns and filter

### Requirement: Compare many experiments

Selecting 2 to 8 experiments and choosing Compare SHALL show their parameters side by side (differences only by default), each metric's last value per experiment with the best marked, every step's scalar charts overlaid, and a chosen `node.output` side by side.

#### Scenario: Sweep

- **WHEN** four experiments that differ only in `train.lr` are compared
- **THEN** the parameter table shows only `train.lr`, and the loss charts overlay four lines labelled by experiment

### Requirement: Experiment tags, notes and archive

Experiments SHALL take free-form tags, a markdown note and an archived flag, set from code, the CLI or the UI, stored outside the experiment's config so they never affect runs or signatures. Archived experiments SHALL be hidden from the table and sidebar unless the user includes them.

#### Scenario: Tag from code

- **WHEN** code calls `exp.tag("ablation")`
- **THEN** filtering the table by `#ablation` shows that experiment

#### Scenario: Archive

- **WHEN** the user archives an experiment
- **THEN** it disappears from the default table and sidebar, still loads by URL, and its storage is unchanged
