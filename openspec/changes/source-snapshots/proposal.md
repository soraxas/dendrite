## Why

A step's signature includes a hash of its function source, but the source itself is thrown away. Users can see *that* code changed (a step re-ran), but not *what* code produced a result or where it lives. They also can't fix a step from the place where they noticed the problem.

## What Changes

- **Snapshot:** each execution records a snapshot of its node function's source text, file path, first line, module and qualified name. Snapshots are content-addressed and de-duplicated. Signatures, and therefore cache reuse, are unchanged.
- **API:**
  - `GET /api/executions/{id}` gains `source` (hash, file, line, qualname).
  - New `GET /api/sources/{hash}` returns the snapshot plus its on-disk status: `same` when the snapshot still appears exactly once in the file, otherwise `changed` or `missing`.
  - New `PUT /api/sources/{hash}` writes edited source back to the recorded file.
- **UI:**
  - The step panel shows the code location and a "View code" / "Edit code" button.
  - This opens an Ace editor: editable when the disk status is `same`, read-only with an explanation otherwise.
  - Save asks for confirmation and warns that the step and everything downstream will re-run.
  - Closing with unsaved edits asks before discarding.
- **Safety:**
  - Writes go only to the recorded path, and only if the file is unchanged since it was loaded (optimistic concurrency via file hash).
  - Only the snapshot's text is replaced, and the write is atomic.
  - Editing is enabled only when `dendrite ui` is bound to a loopback address, or with `--allow-edit`.

## Capabilities

### New Capabilities

- `source-snapshots`: capturing, serving and safely editing the source code of pipeline steps.

### Modified Capabilities

<!-- none: the lineage-ui and execution-cache behaviour is unchanged; this adds endpoints and a panel section -->

## Impact

- **`db.py`:** new tables `code_snapshots` and `execution_sources`, created automatically on existing databases.
- **`task.py`:** captures the snapshot when an execution starts.
- **`server.py`:** source endpoints and the `allow_edit` flag. **`cli.py`:** `--allow-edit`.
- **UI:** `ace-builds` (pinned), plus a code modal in the step panel.
- **Tests:** snapshot capture, disk status, the save success path, stale-file conflict, and edit-disabled refusal.
