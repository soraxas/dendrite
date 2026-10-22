## Context

`add-artifact-lineage` shipped a single static `index.html`. It used vanilla JS and a hand-written layered SVG layout (decision D10: no Node toolchain). In practice:

- Edges that skip layers run behind nodes or bow awkwardly.
- The lineage view's labels, legend and side panel confuse users (see proposal).
- Pan, zoom and selection are fragile because the page re-renders by replacing `innerHTML` every poll.

Prefect, the orchestrator dendrite builds on, ships its own React UI by building it in CI into a git-ignored package directory. A hatch build hook refuses to package a wheel without the bundle (`../prefect/hatch_build.py`, `pyproject.toml [tool.hatch.build] artifacts`). JupyterLab, MLflow and Dagster follow the same pattern.

## Goals / Non-Goals

**Goals:**
- Readable, interactive graphs for the experiment DAG and artifact lineage.
- A lineage view that makes data vs computation and the selected item obvious.
- `pip install dendrite` and `dendrite ui` still work with no Node.js.
- A fast local UI loop with hot reload against a real `dendrite ui` backend.

**Non-Goals:**
- Changing the Python lineage engine, caching or storage semantics.
- Auth, multi-user features or server-side push (polling stays).
- Publishing to PyPI or setting up CI. A local `scripts/build-ui.sh` mirrors what CI would do.
- Visual regression testing.

## Decisions

### D1. React + TypeScript + Vite, source in `ui/`
- The source lives at the repo root, outside the Python package, so npm tooling never leaks into the wheel.
- Vite builds to `src/dendrite/ui/dist/` with `base: "./"`, so asset URLs work under any mount path.
- *Alternatives:*
  - CDN-loaded ESM with no build: needs internet on every load and has no types.
  - Svelte or Vue: React Flow is the strongest maintained graph library, and it is React-only.

### D2. React Flow (`@xyflow/react`) + elkjs for layout
- React Flow provides pan, zoom, a minimap, custom nodes, selection and incremental updates. Node positions are diffed instead of rebuilding the DOM.
- elkjs `layered` handles direction, ports and edge routing for long edges far better than dagre.
- Layout runs in a web worker build of elkjs (`elkjs/lib/elk-worker`), so large lineage graphs don't block the UI thread.
- Positions are cached per graph key. The layout is recomputed only when the *set* of nodes or edges changes, not on every status poll, which keeps live updates stable.
- *Alternative:* dagre. It is simpler, but its edge routing is the weakness we are trying to fix.

### D3. Data fetching with TanStack Query
- The 1s poll while a run is active, and 4s when idle, uses `refetchInterval` as a function of the last result.
- Responses are cached per route, with no manual timers.

### D4. Lineage model gains labels and structured roles (API is additive only)
- Each entry in `lineage().artifacts` gains:
  - `label`: the producing execution's output name. Sources use the experiment input name, then fall back to the filename and then the kind.
  - `producer_node`
  - `role`: `focus`, `ancestor` or `descendant`
- `experiments_using_artifact` returns `roles: [{kind: "input"|"produced"|"read", node}]`. The old string list stays alongside it as `role_labels` for the CLI.
- Labels are derived in Python, not in the UI, so the CLI and Mermaid output benefit too.

### D5. Per-view legends and node vocabularies
- **DAG view:** pipeline-node statuses (queued, running, waiting, computed, cached, pinned, failed) plus input nodes.
- **Lineage view:** artifact nodes (data), execution nodes (computation), and the traced artifact (accent). Status colours are not reused there.

### D6. Selection is a single source of truth
- Selection lives in the URL (`?sel=<kind>:<id>`), so it survives polling, reload and sharing links.
- The graph highlights the selected node and its incident edges.
- The detail panel renders from the same selection, with a header like "Selected · artifact report".

### D7. Packaging: hatchling with `artifacts` + build hook (mirrors Prefect)
- `uv_build` has no build hooks and cannot guard against a missing bundle, so the backend switches to `hatchling`.
- `[tool.hatch.build] artifacts = ["src/dendrite/ui/dist"]` includes the git-ignored bundle.
- `hatch_build.py` raises when `dist/index.html` is missing for `sdist` or `wheel` targets. Editable builds are exempt, so `uv sync` keeps working without Node.
- `DENDRITE_SKIP_UI_CHECK=1` is an escape hatch for building Python-only wheels deliberately.
- *Alternative:* commit `dist/`. It works with `pip install git+…`, but it creates noisy diffs and stale bundles.

### D8. Serving and fallback
- `create_app` mounts `dist/` with `StaticFiles` and serves `dist/index.html` for SPA routes when `dist/index.html` exists.
- Otherwise it serves the legacy single-file page (`legacy.html`, the current UI moved).
- `/api/info` reports `ui: "react" | "legacy"`.
- The SPA uses hash routing, unchanged from today, so FastAPI needs no catch-all route. Deep links keep working whichever UI is served.

## Risks / Trade-offs

- [Contributors without Node get the legacy UI and may not notice] → `/api/info.ui` is shown in the header, and `dendrite ui` prints a one-line hint when serving the legacy page.
- [Two UIs drift] → The legacy page is frozen as a fallback only, with no new features. It is removed once wheels are always built with the bundle.
- [Bundle size: React Flow + elk is ~1.5 MB minified] → elk is loaded in a worker and code-split for the lineage route. This is acceptable for a local tool.
- [Switching the build backend changes what is packaged] → A test builds the wheel and asserts that `dendrite/ui/dist/index.html` and `dendrite/ui/legacy.html` are present.
- [elk layout is async, so the graph would flash] → Cached positions are rendered while re-layout runs, and new nodes fade in.

## Migration Plan

1. Archive `add-artifact-lineage` so `lineage-ui` becomes a baseline spec.
2. Land the API additions first. They are backward compatible, and the legacy UI ignores them.
3. Move `index.html` to `legacy.html` and switch the server to dist-or-legacy.
4. Add `ui/` and the hatch hook. Build and verify the wheel.

Rollback: delete `ui/dist` and the server falls back to `legacy.html`.

## Open Questions

- Should the minimap be on by default for small DAGs (under 10 nodes)?
- Should the lineage depth be limited (for example "N hops") for very large provenance graphs, or is collapsing by execution enough?
