## Why

The build-free SPA from `add-artifact-lineage` works, but it is hard to read:

- Its hand-rolled layered SVG layout tangles edges that skip layers.
- The lineage screen labels artifacts by storage kind ("value") instead of what they are ("report").
- It reuses the run-status legend where the colours mean something else (data vs computation).
- Its side panel shows a different item than the header with no visual link between them.
- It prints "used by" roles as raw strings such as `consumed:colmap`.

A proper graph library plus a component framework fixes the rendering problems and makes the UI maintainable as it grows. To keep `pip install dendrite` free of Node, it should ship the way Prefect, JupyterLab and MLflow do: pre-built static assets inside the wheel.

## What Changes

- **BREAKING (UI only).** The SPA in `src/dendrite/ui/index.html` is replaced by a React + TypeScript + Vite app in `ui/`, built with:
  - React Flow (`@xyflow/react`) for graphs
  - elkjs for layered layout and orthogonal/spline edge routing
  - TanStack Query for polling the API

  The JSON API stays backward compatible except for one change: artifact usage `roles` becomes structured, and the old strings move to `role_labels`.
- The experiment DAG and artifact lineage are rendered with React Flow custom nodes:
  - status-coloured pipeline nodes with live animation
  - distinct artifact (data) nodes and execution (computation) nodes
  - minimap, fit-view, and selection highlighting of connected edges
- Lineage screen fixes:
  - Artifacts are labelled by their **output name** (e.g. `report`), or the input or file name for sources. The kind and hash are secondary text.
  - Each view has its own legend. Lineage uses "data / computation / traced artifact", not run statuses.
  - The side panel always describes the selected graph item. That item is highlighted in the graph, and the panel header names it.
  - "Used by" is grouped per experiment with readable roles: *produced by extract_frames*, *read by colmap, train_splats*.
- API additions for the lineage graph:
  - `label`, `producer_node` and `role` on artifacts
  - structured `roles` (`{kind, node}`) in `experiments_using_artifact`
- Packaging:
  - Built assets go to `src/dendrite/ui/dist/`, which is git-ignored.
  - The build backend moves from `uv_build` to `hatchling` with `artifacts` and a custom build hook. The hook fails sdist or wheel builds when the bundle is missing, unless the build is editable.
  - The FastAPI app serves `dist/` when present, and otherwise falls back to the legacy single-file page, kept as `ui/legacy.html` inside the package.
- Developer workflow:
  - `npm run dev` runs a Vite dev server with `/api` proxied to `dendrite ui`.
  - `npm run build` writes into the package.
  - A CI-equivalent `scripts/build-ui.sh` runs before `uv build`.

## Capabilities

### New Capabilities
- `ui-packaging`: how the web UI is built, bundled into the wheel, guarded at build time, served, and falls back when unbuilt.

### Modified Capabilities
- `lineage-ui`: the SPA requirements change to cover a React Flow graph, per-view legends, output-name artifact labels, selection-linked detail panel, readable usage roles, and the added API fields.

> Requires `add-artifact-lineage` to be archived first, so that `openspec/specs/lineage-ui/spec.md` exists as the baseline for the MODIFIED deltas.

## Impact

- **New:**
  - `ui/` (package.json, Vite config, TS sources)
  - `scripts/build-ui.sh`
  - `hatch_build.py`
- **Changed:**
  - `pyproject.toml` (build backend, artifacts, hook)
  - `server.py` (static mounting, fallback, new fields)
  - `experiment.py` / `db.py` (lineage labels and structured roles)
  - `.gitignore`
- **Moved:** `src/dendrite/ui/index.html` becomes `src/dendrite/ui/legacy.html`.
- **Dependencies:**
  - Node.js 20+ is needed *only* to build or develop the UI, never to install or run dendrite.
  - npm packages: react, react-dom, @xyflow/react, elkjs, @tanstack/react-query, typescript, vite.
- **Tests:**
  - API tests cover the new fields and fallback serving.
  - A packaging test asserts that the wheel contains `dist/index.html`.
  - UI type-check and build run via `npm run build`.
