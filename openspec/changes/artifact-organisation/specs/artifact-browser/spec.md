## ADDED Requirements

### Requirement: Per-experiment artifact list

The UI SHALL offer an Artifacts tab for each experiment (`#/exp/:id/artifacts`) listing the experiment's inputs and every node run's outputs, grouped by stage and then by step, in pipeline order. Each row SHALL show the output name, kind, size, a thumbnail when a preview exists, its names, and whether this experiment computed it, reused it (with the experiment that first computed it), or pinned it. Fan-out items SHALL collapse under their step with a count and expand on demand. The list SHALL come from data the API already returns plus artifact names, and SHALL NOT download blobs to build it.

#### Scenario: Reused output

- **WHEN** a fork reuses `colmap.cameras` computed in its parent
- **THEN** the row for `colmap.cameras` shows "reused" and links to the parent experiment

#### Scenario: Fan-out step

- **WHEN** a step ran 200 fan-out items
- **THEN** the step shows one collapsed group with "200 items", and expanding it lists the items

#### Scenario: Selecting a row

- **WHEN** the user clicks a row
- **THEN** the artifact detail panel opens for that artifact, as selecting it on the graph does

### Requirement: Filter artifacts

The Artifacts tab SHALL filter rows by kind (image, video, 3D, directory, value, other) and by text matched against step, output and name. Filters SHALL live in the URL so a filtered view can be shared.

#### Scenario: Only videos

- **WHEN** the user picks the video filter
- **THEN** only rows whose artifact is a video remain, and their groups stay in pipeline order

### Requirement: Compare one output across forks

From any output row the user SHALL be able to open Compare for that `node.output`, showing it side by side for this experiment, its parent and its direct forks that produced it. Each column SHALL be labelled with the experiment name and its param differences from this experiment, and SHALL use the existing preview components. Identical hashes SHALL be marked as the same artifact rather than shown as different.

#### Scenario: Compare renders

- **WHEN** the user compares `render.video` on an experiment with two forks
- **THEN** three previews appear, each labelled with its experiment and changed params

#### Scenario: Same result

- **WHEN** a fork produced the same hash for the compared output
- **THEN** its column says it is identical to the base instead of loading the preview again
