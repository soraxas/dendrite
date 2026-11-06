## Context

Artifacts are identified by content hash and shared across experiments. One hash can be the output of many node runs. What the UI calls an artifact is reached through an experiment's graph. `dag()` already returns each node's outputs, its `status` (`done`, `cached`, `pinned`, …) and `execution_experiment`, the experiment whose execution produced it. `ArtifactIcon.artifactCategory` already classifies artifacts by kind and extension. Reclamation (`storage.py`) computes reachability from per-experiment roots and treats the pins of living experiments as untouchable. The hosted server runs every `Metadata` method through `wire.REMOTE` with a role, and `ServerMetadata` checks writes against `CALLER`.

## Goals / Non-Goals

**Goals:**

- Find and reuse important artifacts by a human-readable name, from code, the CLI and the UI.
- See everything one experiment read and produced, organised the way the pipeline is.
- Compare one output across forks without clicking through graphs.
- Make "this matters" also mean "GC keeps it".

**Non-Goals:** folders, renaming outputs, cross-experiment boards, names inside signatures or plans.

## Decisions

### 1. A name points at a hash, with its source as context

`artifact_names(name PK, hash, experiment_id NULL, node NULL, output NULL, note, set_by NULL, set_at)`. The target is the hash, because that's what `load`, pins and GC understand. The source is (experiment, node, output). It isn't part of the identity, but it lets the UI open the artifact "within" the run it came from and lets Compare work from a name. There's no foreign key to `experiments`: deleting an experiment keeps its names, and the UI shows the source as "deleted experiment", just as executions already are.

*Alternative:* naming a (experiment, node, output) address instead of a hash. This was rejected because re-running replaces node runs, so the target would change silently. A hash is immutable, and immutability is the point.

### 2. History is a separate append-only table

`artifact_name_history(id PK autoinc, name, hash, experiment_id, node, output, note, set_by, set_at, ended_by, ended_at, action)`, where `action` is `moved` or `removed`. A move or a remove writes the outgoing row to history and updates or deletes the live row in **one transaction**, as a compare-and-set on the old hash (the same idea as `update_experiment_if`). Two concurrent moves can't both win. The loser gets a conflict, and the CLI and UI show the new target.

*Alternative:* a single table with `valid_to`. This was rejected because it makes the "unique current name" constraint and the hot lookup harder on both SQLite and Postgres.

### 3. `@name` resolves at the edge, and config stores hashes

`Lab.artifact(key)` checks `key.startswith("@")` before hash and prefix lookup. `Lab.ingest(source)` does the same for strings, so `inputs={"images": "@raw-scan"}` never touches the filesystem. Hashes and prefixes are hex and can never start with `@`. A file literally named `@x` must be passed as a `Path`. `_resolve_pin` already goes through `Lab.artifact`, so pins work without further changes. Because `create`/`fork` store hashes, frozen config is unaffected when a name moves. That is the experiment invariant.

### 4. Names are GC roots outside any experiment

`Storage._roots` gains a synthetic group (`"@names"`) holding every current name's hash. Its closure covers directory members. Per-experiment "exclusive bytes" must not count named artifacts as freeable, so named hashes are subtracted from what deleting an experiment would free. `evict` checks names the same way it checks pins and raises `EvictRefused` listing them. History rows protect nothing: removing a name makes its artifact collectable again. Reclamation still only deletes blobs, so the "reclaiming never risks a wrong result" invariant is untouched.

### 5. API shape: one names map per response

DAG, artifact and lineage responses gain a top-level `names: {hash: [name, …]}` covering the hashes they mention. This avoids repeating names inside every edge or output. One `Metadata.names_for(hashes)` query fills it. `change_tokens()` includes a cheap fingerprint of `artifact_names` (count plus max `set_at`), so naming in another process pushes an SSE update. Mutations are `PUT /api/names/{name}` with body `{target, note?, move?, expect?}` and `DELETE /api/names/{name}?expect=<hash>`. `expect` carries the compare-and-set, and a mismatch gives 409 `name_moved`. All mutations are gated by `allow_edit`.

Target strings (`exp:node.out`, `exp:input`, hash or prefix) are parsed in one Python helper, `Lab.resolve_target`, shared by the CLI and the server. The UI always sends structured targets (`{experiment, node, output}` or `{hash}`).

### 6. Hosted: identity comes from the server, and roles mirror keep

The new `Metadata` methods are listed in `wire.REMOTE`:

- reads (`names`, `names_for`, `name_history`): `viewer`
- `set_name`, `remove_name`: `runner`

`ServerMetadata` overwrites `set_by` with the caller and never trusts the client. Moving or removing a name whose `set_by` differs from the caller needs `admin`, checked in `hosted/authz.py`. A name may only target a hash present in `artifacts` for that project. A name never needs new blobs, so the "publish needs committed blobs" rule holds trivially. `export_bundle` includes current names but not history. `import_bundle` inserts missing names, skips a name whose current target differs, and returns the skipped names for `push` to print.

### 7. Artifacts tab is UI-only, built from `dag()`

`components/Artifacts.tsx` derives its rows from the DAG query that is already cached:

- inputs, then nodes in topological order, grouped by `stage` and then step
- fan-out `step[key]` nodes grouped through `fanout.ts`
- source: `cached` → "reused from `execution_experiment`", `pinned` → "pinned", otherwise "computed"

Kinds come from `artifactCategory`. Thumbnails are lazy (IntersectionObserver), images only, and only below `auto_preview_bytes`. Everything else shows `ArtifactIcon`. Filters live in the hash URL as `?kind=&q=`, next to `sel`, so a row click sets `sel=artifact:<hash>` and reuses the existing detail panel.

### 8. Compare uses existing endpoints

Compare takes `node.output` and finds its candidates from the experiment list already loaded (this experiment, its `parent_id`, and experiments whose `parent_id` is this one). It fetches each candidate's DAG through TanStack Query (cached) and `GET /api/diff` for param differences. Columns reuse `ArtifactPreview`. Equal hashes collapse to "identical to base". There's no new server endpoint. Candidates are capped at 8, with a "show more" link, so a heavily forked experiment doesn't fan out dozens of DAG fetches at once.

## Risks / Trade-offs

- **Collisions in a flat namespace** in a busy hosted project: people may fight over `best`. → Mitigation: notes, attribution, admin-only takeover. Prefixes (`alice/best`) aren't allowed, because `/` is rejected to keep URLs simple. Namespaces can come later if needed.
- **Names keep large blobs alive forever.** → Mitigation: the Storage page lists named artifacts with their size under "protected", and removing a name is one click.
- **A deep directory as a GC root** adds its manifest members to the closure. The cost is the same as pins today.
- **DAG fetches in Compare** load several experiments' graphs. → Mitigation: the cap, plus query caching. A dedicated endpoint can follow if it's slow.
- **A file named `@foo` as an input** is now read as a name. → Mitigation: this is documented, `Path("@foo")` still works, and the error message for an unknown name suggests it.

## Migration Plan

New tables only, created by `create_all` on connect, as with every other table, so existing labs and hosted projects gain them on first start. Rollback is dropping the two tables; nothing else references them.

## Open Questions

- Should a name optionally be scoped to a pipeline, so `best` can exist once per pipeline? The default is no: one flat namespace per lab or project.
- Should the Names page offer "use as pin in a new draft"? It's a natural follow-up once drafts accept `@name`, but it's out of scope here.
