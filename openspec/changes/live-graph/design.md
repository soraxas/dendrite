## Decisions

### D1. Server-Sent Events, not WebSockets
Updates only flow from server to browser, and SSE covers that. It reconnects on its own (`retry: 2000`), works through proxies, and needs no protocol layer. Events carry only *what changed* (`{"tree": bool, "experiments": [ids]}`); clients refetch through the existing JSON endpoints, so there is no second data model to keep in sync.

### D2. Change detection by database fingerprint
A run usually executes in another process (`dendrite run`, `data_pipeline.run`), so the server can't hear it directly. `Metadata.change_tokens()` gives:
- one fingerprint for the experiment list
- one per experiment, covering its rows, node-run counts and latest timestamps per status, and its latest progress write

The stream compares fingerprints every 0.25 s and sends a keep-alive every 15 s. Postgres LISTEN/NOTIFY could later replace the polling, without any client change.

### D3. Progress belongs to the execution
`execution_progress` is keyed by execution, because an execution is shared: an experiment waiting on a lease for identical work sees the same progress. The running node run now gets its `execution_id` when the execution starts, so the DAG can join them. Progress is display-only and never part of a signature.

### D4. Body reads
`runtime.note_read` records a read when a pipeline run is active and no step is executing. `after` is the number of nodes called at that point. Reads are stored per experiment and cleared on rerun. Plans collect them in memory and never write them.

The UI draws a dashed "decides" edge from a read step to the nodes called after the read that no later node feeds. Queued nodes are skipped, because their inputs aren't known until they start. Reads of experiment inputs get no edges, since they'd connect to everything; the input is marked "configures this run" instead.

### D5. Fan-out grouping is a view
Nodes named `step[key]` (from `step.named`) group by step in the UI only. Groups of `COLLAPSE_AT` (4) or more start collapsed, and the user's toggles flip the default per experiment.

While collapsed:
- edges are redirected to the group card, with merges and self-loops dropped
- selecting a member highlights the group
- the group's panel lists its members
