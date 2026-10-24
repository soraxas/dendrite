## 0. Prerequisite

- [x] 0.1 Archive `add-artifact-lineage` (`openspec archive add-artifact-lineage`) so `openspec/specs/lineage-ui/spec.md` exists as the baseline for the MODIFIED deltas

## 1. API additions (backward compatible)

- [x] 1.1 Add `label`, `producer_node` and `role` to each artifact in `Lab.lineage()`. The label is the producer's output name; sources fall back to the experiment input name, then the filename, then the kind
- [x] 1.2 Return structured `roles: [{kind, node}]` from `experiments_using_artifact`, keeping the string list as `role_labels`, and update the CLI `lineage` output to use labels
- [x] 1.3 Use artifact labels in `lineage_mermaid`

## 2. Packaging and serving

- [x] 2.1 Move `src/dendrite/ui/index.html` to `src/dendrite/ui/legacy.html`
- [x] 2.2 In `server.py`, serve `ui/dist` via `StaticFiles` when `dist/index.html` exists, otherwise serve `legacy.html`. Add `ui` to `/api/info`, and have `dendrite ui` print a hint when serving legacy
- [x] 2.3 Switch the build backend to `hatchling`: wheel `packages`, `[tool.hatch.build] artifacts = ["src/dendrite/ui/dist"]`, and a custom hook entry
- [x] 2.4 Add `hatch_build.py`, which fails sdist and wheel builds when `dist/index.html` is missing (skipping editable builds, and honouring `DENDRITE_SKIP_UI_CHECK=1`)
- [x] 2.5 Add `src/dendrite/ui/dist/` and `ui/node_modules/` to `.gitignore`
- [x] 2.6 Add `scripts/build-ui.sh`, which runs `npm ci && npm run build` in `ui/`

## 3. React app scaffold

- [x] 3.1 Create `ui/` with Vite + React + TypeScript: `outDir: ../src/dendrite/ui/dist`, `base: "./"`, and a dev proxy from `/api` to `http://127.0.0.1:8454`
- [x] 3.2 Add the dependencies `@xyflow/react`, `elkjs` and `@tanstack/react-query`. Pin exact versions in `package.json` and commit `package-lock.json`
- [x] 3.3 Add typed API client and response types matching `server.py`
- [x] 3.4 Add hash routing (`#/exp/:id[/params|metrics|diff/:other]`, `#/artifact/:hash[/from/:exp]`) with the selection held in a `sel` query param
- [x] 3.5 Add theme tokens (light and dark via `prefers-color-scheme`), ported from the legacy CSS variables

## 4. Graph views

- [x] 4.1 Add an `useElkLayout` hook: elk-worker layered layout, a direction chooser (left-to-right or top-to-bottom by viewport aspect), and positions cached per node/edge-set key
- [x] 4.2 Build the experiment DAG with React Flow. It needs custom `pipelineNode` and `inputNode` components, merged parallel edges with combined labels, status animation for running and waiting, a minimap, fit-view and controls
- [x] 4.3 Build the lineage graph. It needs `artifactNode` (labelled by output name, with kind, hash and size as secondary text) and `executionNode`, plus focus-artifact accent styling and highlighting of the selection's incident edges
- [x] 4.4 Add per-view legends: run statuses for the DAG; data, computation and traced artifact for lineage

## 5. Panels and pages

- [x] 5.1 Add the sidebar fork tree with a filter and per-experiment status bars, polled through TanStack Query (1s while running, 4s when idle)
- [x] 5.2 Add the toolbar: experiment name, status, parent link, the DAG/Params/Metrics tabs, and a Compare picker that is hidden when no other experiment exists
- [x] 5.3 Add the detail panel, driven by the URL selection, with a header like "Selected · <kind> · <label>". It covers node, artifact (preview, download, grouped "used by") and execution (signature spec, reused-by) views
- [x] 5.4 Add the Params, Metrics and Diff pages, ported from legacy, with neutral metric deltas, and metric sparklines in the node panel

## 6. Verification

- [x] 6.1 Update `tests/test_api.py` to cover lineage artifact `label`, `producer_node` and `role`, structured `roles` in artifact detail, and `/api/info.ui == "legacy"` when no bundle exists
- [x] 6.2 Add `tests/test_server_static.py`, which places a fake `dist/index.html` plus an asset in a temp package dir, and asserts that `/` serves it, assets resolve, and `ui == "react"`
- [x] 6.3 Add `tests/test_packaging.py`, which builds a wheel with `DENDRITE_SKIP_UI_CHECK` unset against a stub bundle, asserts that `dist/index.html` and `legacy.html` are in the wheel, and asserts the build fails when the bundle is missing. Mark it slow and skip it when `uv` is unavailable
- [x] 6.4 Check that `npm run build` passes (tsc type-check plus vite build), and that the lineage and live DAG views match the spec scenarios via headless Chrome screenshots against `examples/recon.py` with `DEMO_SLEEP=2`
- [x] 6.5 Update `README.md` and `CLAUDE.md` with the UI dev loop, the build/release steps, the legacy fallback, and the new Node requirement for UI work only

## 7. UX clarity pass

- [x] 7.1 Add a shared user-facing status vocabulary (`ui/src/copy.ts`) used by nodes, legends, sidebar, panels and diff
- [x] 7.2 Sidebar: heading with a fork hint, compact indentation, plain outcome line ("Reused 2 of 4 steps"), status glyph only for states that need attention, and the id moved to a tooltip
- [x] 7.3 Top bar: replace db/store paths with a "Lab" popover, and show a live indicator only while something runs
- [x] 7.4 Toolbar: story line (changes from parent, steps reused, time saved), Pipeline/Parameters/Metrics tabs, and a labelled "Compare with"
- [x] 7.5 Node hover cards via `NodeToolbar`; legends show only the outcomes present; input nodes show filename and size
- [x] 7.6 Lineage mode: mode chip, tinted cross-hatched canvas, banner with "across all experiments" and a back button, and controls/minimap kept clear of the banner
- [x] 7.7 Detail panel: "Changed from <parent>" table instead of raw JSON, outcome explanation, and ids, signatures and Prefect run tucked under "Technical details"
- [x] 7.8 Visual check with `scripts/screenshot.py` (now supports hover) for the pipeline, a hover card, and lineage
- [x] 7.9 Sidebar: 232px wide, collapsible to a 40px rail (remembered per browser), hint moved to an ⓘ tooltip, and relative last-run time per experiment (`started_at`/`finished_at` added to the tree API)
- [x] 7.10 Selected step shows its input chips on the incoming side and output chips on the outgoing side; picking a chip opens that data's panel while the step stays selected and the chip highlighted
- [x] 7.11 Re-fit the graph when the canvas is resized (e.g. sidebar collapse)

## 7. Follow-up

- [x] 7.1 Remove the legacy single-file page (`legacy.html`), `role_labels` and the old `?forks=1` spellings: without a bundle the server now serves the API with a build hint
