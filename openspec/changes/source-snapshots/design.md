## Context

`ArtifactTask` computes `code_fingerprint(fn)` (sha256 of `inspect.getsource`) for the signature and discards the text. Executions are immutable records in `executions`. Adding columns to existing tables would need a migration (there's no Alembic), whereas new tables are created by `create_all` on connect.

## Goals / Non-Goals

**Goals:** show exactly what code produced a result and where it lives; allow fixing it in place safely.

**Non-Goals:**
- Snapshotting helper functions or whole modules.
- A git integration.
- Multi-user edit locking.
- Running the pipeline from the UI.

## Decisions

### D1. Separate, content-addressed snapshot tables
- `code_snapshots(hash PK, source, file, line, module, qualname)` stores each distinct source text once.
- `execution_sources(execution_id PK, source_hash)` links executions to snapshots.
- Neither touches the signature spec, so existing caches remain valid.
- Captured at execution start, so failed runs also show their code.
- *Alternative:* store the source inside the `executions.spec` JSON. Rejected, because the spec is what gets hashed into the signature.

### D2. "Still the same" means the exact snapshot text occurs once in the file
- Line numbers drift as files are edited. A verbatim, single occurrence is robust and is the precondition for a safe replace.
- Multiple occurrences or none count as `changed` (read-only). A missing file counts as `missing`.

### D3. Write path is server-chosen, guarded by optimistic concurrency
- `PUT` takes `{source, file_sha}`. The server re-reads the recorded file and checks `sha256(file) == file_sha` and the single-occurrence rule.
- It then replaces the occurrence and writes atomically (temp file + `os.replace`, preserving mode). A mismatch returns 409.
- The path is never taken from the request.

### D4. Editing is opt-in beyond localhost
- `create_app(allow_edit=False)` defaults to refusing writes with 403.
- `dendrite ui` enables editing when `--host` is a loopback address, or when `--allow-edit` is passed.
- `/api/info.edit` tells the UI whether to offer editing.

### D5. Ace editor, lazy-loaded
- `ace-builds` is pinned and loaded only when the modal opens, keeping the main bundle small. It uses the Python mode and a theme that follows `prefers-color-scheme`.

## Risks / Trade-offs

- [The browser can now write files] → loopback-only by default, recorded path only, hash check, and a confirm dialog.
- [Edits invalidate caches] → this is the intended behaviour. The save confirmation says the step and its descendants will re-run.
- [Source captured from notebooks or `exec`] → `inspect` fails, so the snapshot is stored without a file and shown read-only as `missing`.

## Migration Plan

New tables are created on next connect. Executions from before this change have no snapshot, and the UI says so.
