## Why

Artifacts are content hashes, so they're perfect for caching but hard to work with as a person. To find "the good splat from last Tuesday" you have to remember which experiment produced it, open its graph and click the right output. Then, to reuse it as a pin or input, you copy a hash. There's no list of an experiment's outputs, no way to compare one output across forks, and no way to say "this one matters". Without that, nothing tells GC to spare it.

## What Changes

- **Artifacts tab (per experiment).** A new view at `#/exp/:id/artifacts` lists every step's inputs and outputs, grouped by stage and then step.
  - Each row shows the output name, kind, size and a thumbnail. It also shows whether this run **computed** the output or **reused** it, and the experiment it was first computed in.
  - Filters by kind (image, video, mesh/3D, directory, value, other) and by text (step, output or name).
  - Fan-out items collapse under their step, with a count.
  - **Compare** shows one output (such as `render.video`) side by side across this experiment, its parent and its forks, along with each one's param differences. It reuses the existing previews.
- **Artifact names.** A name is a human-readable label (`best-splat`, `paper-fig3`) that a user attaches to an artifact, with an optional note. The UI shows it as a ★ star.
  - **What a name points at:** an artifact hash, plus where it was named from (experiment, node, output) when there is one. Names are unique per lab (per project when hosted).
  - **Re-pointing:** moving a name to another artifact keeps the earlier targets as history, and the history is listed.
  - **Removing:** a name can be removed. The artifact is untouched.
  - **Never in a signature:** names are metadata only, like stages and progress. Naming, re-pointing or removing a name never changes a cache key or a result.
  - **Usable wherever a hash is accepted:** `lab.load("@best-splat")`, `lab.artifact("@best-splat")`, experiment inputs (`inputs={"images": "@raw-scan"}`), and pins (`pin={"colmap.cameras": "@good-cams"}`). An `@name` is resolved to its hash when the experiment is created, so frozen config stays frozen even if the name moves later.
  - **Kept by GC:** an artifact a name currently points at, together with its directory members, is a reclamation root. GC and age-based GC never delete it, and evicting it is refused unless forced, just like a pinned output. The Storage page marks these as **named**.
  - **CLI:**
    - `dendrite name <name> <target> [--note ...] [--force]`, where the target is `<exp>:<node>.<out>`, `<exp>:<input>` or a hash or hash prefix.
    - `dendrite names [--history <name>]`
    - `dendrite unname <name>`
  - **UI:**
    - A ★ button and a name field on the artifact detail panel and on rows of the Artifacts tab.
    - Name badges on graph outputs.
    - A **Names** page (`#/names`) listing every name with its thumbnail, kind, size, source experiment and history.
  - **API:**
    - `GET /api/names`
    - `PUT /api/names/{name}` to set or re-point
    - `DELETE /api/names/{name}`
    - `GET /api/names/{name}/history`
    - Artifact, DAG and lineage payloads carry the names pointing at each hash.
    - Writes require editing to be enabled.
  - **Hosted mode:** names are per project and record who set them. A `runner` can create names and re-point their own. Re-pointing or removing someone else's name needs `admin`, just as with keep flags.
  - **`dendrite push`:** names are carried along. A name that already exists on the server with a different target is skipped and reported, never overwritten.
- **Non-goals:**
  - folders or hand-built trees (experiment → stage → step already gives the structure)
  - renaming outputs or changing `ArtifactRef.filename`
  - boards or collections across experiments (a possible follow-up)
  - names that enter signatures or influence planning

## Capabilities

### New Capabilities

- `artifact-names`: human-readable, re-pointable names for artifacts, with history, `@name` resolution in load, inputs and pins, GC protection, CLI, API and hosted permissions.
- `artifact-browser`: the per-experiment Artifacts tab (grouping, provenance, filters) and the compare-across-forks view.

### Modified Capabilities

- `artifact-store`: reclamation roots now include named artifacts, and eviction refuses named outputs unless forced.
- `lineage-ui`: graph outputs and artifact detail show names and allow starring and naming. The experiment view gains an Artifacts tab, and there is a Names page.

## Impact

- **`db.py`:** two new tables (`artifact_names`, `artifact_name_history`), created by `create_all` with no migration. `Metadata` gains name CRUD plus inclusion in `export_bundle`/`import_bundle`.
- **`experiment.py`:** `Lab.artifact`/`Lab.ingest` resolve `@name`. `Lab.name()/unname()/names()` are added.
- **`storage.py`:** named hashes join the roots. `evict` refuses them.
- **`server.py`:** the name endpoints, plus names on artifact, DAG and lineage responses.
- **`cli.py`:** `name`, `names` and `unname`.
- **`hosted/wire.py`, `hosted/authz.py`:** the new methods in `REMOTE` with roles, and ownership checks.
- **`push.py`:** names travel with the bundle.
- **UI:**
  - new `components/Artifacts.tsx`, `components/Compare.tsx` and `components/Names.tsx`
  - changes to `router.ts`, `DetailPanel.tsx`, `nodes.tsx` (badges), `Storage.tsx` and `api.ts`/`queries.ts`
- **Dependencies:** none.
