## 1. Capture

- [x] 1.1 Add the `code_snapshots` and `execution_sources` tables, plus `record_source` and `source_for_execution` / `snapshot` lookups
- [x] 1.2 Capture the source text, file, line, module and qualname in `ArtifactTask`, and record it when an execution starts

## 2. API

- [x] 2.1 Add `source` to `GET /api/executions/{id}`, and `GET /api/sources/{hash}` with the disk status and file hash
- [x] 2.2 Add `PUT /api/sources/{hash}`: single-occurrence replace, file-hash check (409), atomic write, and the `allow_edit` gate (403)
- [x] 2.3 Add `edit` to `/api/info`; `dendrite ui` enables editing on loopback or with `--allow-edit`

## 3. UI

- [x] 3.1 Show the code location plus a View/Edit button in the step panel
- [x] 3.2 Add a lazy-loaded Ace editor modal (Python mode, theme follows the system), read-only with an explanation unless the status is `same` and editing is allowed
- [x] 3.3 Add a save confirmation that warns about re-running the step and its downstream steps, a 409 → reload prompt, and a confirm-before-discard on close

## 4. Verification

- [x] 4.1 Add `tests/test_sources.py`, covering snapshot capture (file, line, text), signatures unchanged, the `same` / `changed` / `missing` statuses, a successful save rewriting only the function, a 409 on a stale hash, and a 403 when editing is disabled
- [x] 4.2 `npm run build` passes, and screenshots of the editable and read-only modals look right
