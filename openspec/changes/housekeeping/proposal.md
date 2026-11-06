## Why

Several months of changes have left loose ends:

- **Unarchived OpenSpec changes.** Implemented changes are still open, so `openspec/specs` no longer describes the system.
- **Keyboard access.** Most clickable text in the UI isn't reachable from the keyboard.
- **An order-dependent test.**
- **Stray files** in the working tree.

None of these is urgent on its own, but together they make the project harder to read and to review.

## What Changes

- **Archive implemented OpenSpec changes** into the baseline specs, after a final read: `draft-experiments`, `storage-management`, `live-graph`, `pipeline-stages`, `react-flow-ui`, `source-snapshots`, `static-analysis`, `hosted-server`, `run-telemetry`, `artifact-organisation`, `easy-adoption`. Then update CLAUDE.md's OpenSpec section to list only the open changes (`experiments-table`, `housekeeping`).
- **Keyboard access across the UI.** Links that are `<a onClick>` without an `href` become buttons styled as links, or real hash links, so they're reachable with Tab and work with Enter. Interactive rows get `tabIndex` and Enter handling, as the Artifacts tab already does. Re-enable Biome's a11y rules where they now fit.
- **Test isolation.** `tests/test_analysis.py::test_cli_check_exit_status` counts every step registered by earlier test modules, so running `test_sources.py` before it fails. Scope `dendrite check` in that test to its own module, or reset the registry in a fixture.
- **CI.**
  - Make the Windows job blocking once it has passed on `main`.
  - Move the test client off the deprecated `httpx`-based Starlette client when FastAPI's replacement is available.
- **Working tree.** Decide on `HANDOFF.md` and `test-ws/`: commit what is useful, delete or ignore the rest.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `lineage-ui`: every interactive element is reachable and operable from the keyboard.

## Impact

- The UI components that use `<a onClick>` (DetailPanel, Sidebar, Storage, Pages, Names, Artifacts, Toolbar) and Biome settings
- `tests/test_analysis.py`, `.github/workflows/python.yml`, `openspec/specs/*` (via archive), CLAUDE.md
