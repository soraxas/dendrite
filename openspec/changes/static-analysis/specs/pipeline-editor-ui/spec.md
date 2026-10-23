## ADDED Requirements

### Requirement: Code mode
The experiment view SHALL offer a Code tab beside Pipeline. Code mode SHALL show the pipeline function's source in an editor beside a graph of its static shape. Both SHALL update as the user types (debounced), without saving.

#### Scenario: Shape follows typing
- **WHEN** a user adds a step call to the pipeline source in the editor
- **THEN** after a short pause the shape graph shows the new node and its edges, and the file on disk is unchanged

### Requirement: Diagnostics in the editor
Diagnostics SHALL appear as gutter markers on their lines, with the message on hover, and as a list under the editor. Selecting a diagnostic SHALL move the cursor to its line.

#### Scenario: Unpacking error
- **WHEN** the user types `a, b, c = colmap(frames)`
- **THEN** that line shows an error marker reading that colmap produces 2 outputs

### Requirement: Uncertain structure is shown honestly
Conditional steps SHALL be drawn dashed, with their condition. Repeated steps SHALL carry a loop badge. Dynamic calls SHALL be drawn as a distinct box showing their call text. The view SHALL say that the shape comes from the source, and that the plan is what decides one experiment's actual steps.

#### Scenario: Conditional step
- **WHEN** a step is called under `if refine:`
- **THEN** its node is dashed and labelled `if refine`

### Requirement: Plan overlay and saving
For a draft, shape nodes SHALL carry their planned outcome colour by name. Saving SHALL use the existing source-write path: confirmation, a conflict check when the file changed, the recorded path only, and only when editing is enabled. Closing with unsaved changes SHALL ask first.

#### Scenario: Save and re-plan
- **WHEN** a user saves an edit that adds a step, while a draft is open
- **THEN** the file is written, and the draft's plan (after the module reloads) includes the new step
