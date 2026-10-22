## MODIFIED Requirements

### Requirement: Lineage HTTP API
The system SHALL expose JSON endpoints for:
- the experiment list
- experiment detail (params, node runs, metrics)
- the experiment DAG (nodes and edges with status)
- the fork tree
- the experiment diff
- artifact detail and lineage
- execution detail

Additional requirements:
- Lineage artifacts SHALL include a human-readable `label`, `producer_node` and `role` (`focus`, `ancestor` or `descendant`).
- Artifact usage SHALL expose structured `roles` (`{kind: input|produced|read, node, name}`). The previous compact role strings move to `role_labels`.
- Every other existing response field MUST remain unchanged.

#### Scenario: DAG endpoint
- **WHEN** `GET /api/experiments/{id}/dag` is requested
- **THEN** the response contains node objects (name, status, signature, execution, outputs, duration) and edges labelled with artifact names

#### Scenario: Lineage artifacts carry output-name labels
- **WHEN** `GET /api/artifacts/{hash}/lineage` is requested for `train`'s `report` output
- **THEN** that artifact has `label: "report"`, `producer_node: "train"` and `role: "focus"`
- **AND** the experiment's source input has `label` equal to its input name

#### Scenario: Structured usage roles
- **WHEN** artifact detail is requested for an extract output that two experiments reuse
- **THEN** each experiment entry lists `{kind: "produced", node: "extract"}` and `{kind: "read", node: "featurize"}`

### Requirement: Live single-page app
The system SHALL serve a single-page app at `/`, built with React Flow. The app SHALL:
- list experiments as a fork tree
- render the selected experiment's DAG, laid out by ELK and colour-coded by status
- update node status in place, without re-layout, while the experiment is running
- show node details, outputs and metrics on selection

#### Scenario: Live progress
- **WHEN** an experiment is running and the UI is open
- **THEN** nodes transition visibly from queued to running to computed or cached, without a page reload
- **AND** status-only changes do not move nodes; the graph is re-laid-out only when nodes or edges are added. For `.submit()` pipelines, an edge appears once its upstream future resolves.

### Requirement: Output and lineage tracing
The system SHALL let a user open any artifact and see its producing execution, its upstream ancestry, its downstream consumers, and the experiments that reference it. In the lineage graph:
- artifacts (data) and executions (computation) SHALL be visually distinct node types
- artifacts SHALL be labelled by output name, with kind and hash as secondary text

#### Scenario: Trace artifact
- **WHEN** a user clicks an output artifact
- **THEN** the UI shows its ancestors and descendants, and links them to their executions

#### Scenario: Output-name labels
- **WHEN** a user traces `evaluate`'s `report` output
- **THEN** the traced node reads "report" rather than its storage kind "value"

## ADDED Requirements

### Requirement: Per-view legends
Each graph view SHALL show a legend for only the encodings it uses:
- The DAG view SHALL show pipeline run statuses.
- The lineage view SHALL show data, computation and the traced artifact. It MUST NOT show run statuses.

#### Scenario: Lineage legend
- **WHEN** the lineage view is displayed
- **THEN** the legend lists "data", "computation" and "traced artifact", and no run-status entries

### Requirement: Selection-linked detail panel
The UI SHALL keep a single selection, encoded in the URL. The graph SHALL highlight the selected node and its incident edges. The detail panel SHALL describe exactly that item, and its header SHALL name the item's kind and label.

#### Scenario: Selecting an artifact in lineage
- **WHEN** a user clicks the `frames` node while tracing `report`
- **THEN** `frames` and its edges are highlighted, the panel header reads "artifact · frames", and the URL contains the selection

#### Scenario: Selection survives polling and reload
- **WHEN** the page polls or is reloaded with a selection in the URL
- **THEN** the same item remains selected and highlighted
