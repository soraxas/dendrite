## 1. Python

- [x] 1.1 `pipeline_reads` and `execution_progress` tables; reads cleared on rerun and on delete
- [x] 1.2 `dendrite.progress` (throttled), `dendrite.load`, `runtime.note_read` from `Lab.load` in a body; plans keep reads in memory
- [x] 1.3 A running node run carries its `execution_id`; DAG/plan nodes get `step`, `key`, `progress`; responses get `reads`
- [x] 1.4 `Metadata.change_tokens` and `GET /api/events` (SSE, `limit` for tests)
- [x] 1.5 Tests: reads and fan-out fields, progress, plan reads not written, events for new experiments and live progress

## 2. UI

- [x] 2.1 `fanout.ts`: groups, default collapse, edge redirection, summaries (partial progress), decides edges, config inputs, re-click selection
- [x] 2.2 Group card with toggle, group detail panel, member collapse control, dashed edges, configuring inputs, progress bars, ticking elapsed time
- [x] 2.3 `useLiveUpdates` (EventSource), live/polling indicator, 15 s safety-net polling while connected
- [x] 2.4 Vitest: fan-out logic, progress text, selection toggle, live hook

## 3. Integration and verification

- [x] 3.1 data-pipeline: `dendrite.load` in the body; per-frame blur progress; recon demo reports training progress
- [x] 3.2 Headless Chrome on a scratch lab: collapsed group, decides edge, configuring input, expand, re-click, live progress from another process

## 4. Follow-ups from review

- [x] 4.1 A queued step records `queued:<step>.<output>` for inputs still being computed, so it is wired to its upstream before that runs (no floating "Queued" cards)
- [x] 4.2 Graph changes animate (nodes glide, new nodes and re-routed edges fade in, animated refit; reduced-motion respected)
- [x] 4.3 Layout direction chosen from the graph against a fixed frame and kept per view: resizing or new steps never flip it
- [x] 4.4 In/out data chips tinted (in: accent, out: green) so they stand out from the canvas
- [x] 4.5 Group cards: an accent `×N` count badge and a stacked edge
