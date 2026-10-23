## ADDED Requirements

### Requirement: Fan-out groups
The pipeline graph SHALL show the keyed copies of one step (`step.named(key)`) as a single collapsible card with the item count, a combined status and combined progress. Large groups SHALL start collapsed. The user SHALL be able to expand and collapse each group, and selecting a group SHALL list its items.

#### Scenario: Many defects
- **WHEN** a run calls `cut_clip.named(i)` for 110 defects
- **THEN** the graph shows one `cut_clip ×110` card, and expanding it shows each item

### Requirement: What the pipeline read
The graph SHALL mark an input the pipeline body read as configuring the run. It SHALL draw dashed "decides" edges from a step whose result the body read to the steps the body called after reading it.

#### Scenario: A plan decides the fan-out
- **WHEN** the body reads `plan_frames`' result and then calls a step per segment
- **THEN** `plan_frames` has dashed "decides" edges to those steps instead of appearing isolated

### Requirement: Deselect by clicking again
Clicking the selected node again SHALL clear the selection and hide its data chips.

#### Scenario: Toggle a node's chips
- **WHEN** a user clicks a selected step
- **THEN** it is deselected and its in/out chips close
