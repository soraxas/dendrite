## 1. Metadata: names and history

- [ ] 1.1 Add `artifact_names` and `artifact_name_history` tables to `db.py` (design §1–2)
- [ ] 1.2 `Metadata.set_name(name, hash, source, note, set_by, move, expect)`: validate the name, refuse a different existing target unless `move`, compare-and-set on `expect`, and write the outgoing row to history in the same transaction
- [ ] 1.3 `Metadata.remove_name(name, expect)`, `name(name)`, `names()`, `names_for(hashes)` and `name_history(name)`
- [ ] 1.4 Add the names fingerprint to `change_tokens()`
- [ ] 1.5 Include current names in `export_bundle`; `import_bundle` inserts missing ones and returns skipped conflicts
- [ ] 1.6 Tests: uniqueness, invalid names, move needs `move`, a concurrent move loses with a conflict, history on move and remove, SQLite and Postgres

## 2. Lab API and `@name` resolution

- [ ] 2.1 `Lab.artifact` and `Lab.ingest` resolve `@name`, with a clear error for unknown names that suggests `Path(...)` for literal files
- [ ] 2.2 `Lab.resolve_target(str)` for `exp:node.out`, `exp:input`, hash or prefix, returning hash plus source
- [ ] 2.3 `Lab.name(name, target, note=None, move=False)`, `Lab.unname(name)` and `Lab.names()`
- [ ] 2.4 Tests: `load("@x")`, inputs and pins by name store hashes and survive a later move, and names never change signatures (the `CALLS` counter shows full reuse)

## 3. Storage protection

- [ ] 3.1 Add the `"@names"` root group in `Storage._roots`, and subtract named hashes from per-experiment exclusive bytes
- [ ] 3.2 `evict` refuses named outputs unless forced, and `EvictRefused` lists the names
- [ ] 3.3 Mark named artifacts as protected in the inventory, and show the protected total
- [ ] 3.4 Tests in `tests/test_storage.py`: GC spares named artifacts and their dir members, age-based GC spares them, a removed name makes the artifact collectable, eviction is refused

## 4. CLI

- [ ] 4.1 `dendrite name <name> <target> [--note] [--force]`
- [ ] 4.2 `dendrite names [--history NAME]` as a table: name, kind, size, source, note, set_by, set_at
- [ ] 4.3 `dendrite unname <name>`
- [ ] 4.4 `dendrite push` prints skipped name conflicts
- [ ] 4.5 CLI tests

## 5. Server API

- [ ] 5.1 `GET /api/names`, `PUT /api/names/{name}` (`{target, note?, move?, expect?}`), `DELETE /api/names/{name}?expect=` and `GET /api/names/{name}/history`, gated by `allow_edit`, with 409 `name_moved` and `name_taken`
- [ ] 5.2 Add a `names` map to the DAG, artifact and lineage responses
- [ ] 5.3 Tests for the endpoints, including 403 without editing

## 6. Hosted mode

- [ ] 6.1 Register the name methods in `hosted/wire.py` `REMOTE` (reads `viewer`, writes `runner`)
- [ ] 6.2 `ServerMetadata` sets `set_by` from `CALLER`, requires the target hash in `artifacts`, and `authz` requires `admin` to move or remove another user's name
- [ ] 6.3 Tests in `tests/test_hosted.py`: attribution, a runner can't take over another user's name, push conflict is skipped; whole suite with `DENDRITE_TEST_REMOTE=1`

## 7. UI: names

- [ ] 7.1 `api.ts`/`queries.ts`: name queries and mutations, and invalidate on SSE
- [ ] 7.2 ★ control and name editor (suggested `<node>-<output>`, note, move, remove, history) in `DetailPanel.tsx`, disabled when editing is off
- [ ] 7.3 Name badges on graph outputs (`nodes.tsx`) from the DAG `names` map
- [ ] 7.4 `#/names` route and `components/Names.tsx` (thumbnail, kind, size, source, note, set_by, history), plus a sidebar link
- [ ] 7.5 Storage page: "protected (named)" marker and total
- [ ] 7.6 Vitest coverage for the name editor and Names page

## 8. UI: Artifacts tab and Compare

- [ ] 8.1 `#/exp/:id/artifacts` route and tab, with `kind` and `q` filters in the URL (`router.ts`)
- [ ] 8.2 `components/Artifacts.tsx`: rows from `dag()` grouped by stage and then step, fan-out groups collapsed with counts, computed/reused/pinned source with links, lazy thumbnails, and row click sets `sel=artifact:`
- [ ] 8.3 `components/Compare.tsx`: base, parent and forks (capped at 8) for one `node.output`, param differences from `/api/diff`, identical hashes collapsed
- [ ] 8.4 Vitest coverage for grouping, filters and Compare's identical-hash handling

## 9. Docs and wrap-up

- [ ] 9.1 README: names (`@name`, CLI, GC protection) and the Artifacts tab
- [ ] 9.2 Update CLAUDE.md's architecture notes for names and the Artifacts tab
- [ ] 9.3 Run `prek run --all-files`, the full pytest suite on SQLite and Postgres, and `npm test`/`npm run build`
