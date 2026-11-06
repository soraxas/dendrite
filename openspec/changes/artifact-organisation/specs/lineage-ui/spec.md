## ADDED Requirements

### Requirement: Star and name artifacts in the UI

The artifact detail panel and Artifacts tab rows SHALL offer a ★ control. Starring SHALL prompt for a name, suggesting `<node>-<output>`, and an optional note. A starred artifact SHALL show its names, each with move and remove actions. Graph output entries SHALL show a badge for named artifacts. The controls SHALL be hidden or disabled when editing is not enabled.

#### Scenario: Star from the detail panel

- **WHEN** the user stars the output `splat` of node `train` and accepts the suggested name
- **THEN** the name `train-splat` is created and a badge appears on `train` in the graph

#### Scenario: Read-only server

- **WHEN** the UI is served without editing
- **THEN** names are shown but the ★ control is disabled

### Requirement: Names page

The UI SHALL provide a Names page (`#/names`) listing every name with its thumbnail, kind, size, source experiment and step, note, who set it (hosted mode) and when. Selecting a name SHALL open its artifact; each name SHALL show its history on demand. The Storage page SHALL mark named artifacts as protected.

#### Scenario: Open a name

- **WHEN** the user clicks `best-splat` on the Names page
- **THEN** the artifact view opens for its current target, within its source experiment when it has one
