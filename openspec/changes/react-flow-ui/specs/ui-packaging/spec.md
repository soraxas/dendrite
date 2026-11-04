## ADDED Requirements

### Requirement: Pre-built UI bundled in the wheel

The system SHALL build the React UI from `ui/` into `src/dendrite/ui/dist/`, a directory excluded from version control. The built files SHALL be included in sdist and wheel distributions, so that installing and running dendrite never requires Node.js.

#### Scenario: Wheel contains the bundle

- **WHEN** `scripts/build-ui.sh` is run and then `uv build --wheel` is run
- **THEN** the wheel contains `dendrite/ui/dist/index.html` and its hashed assets

### Requirement: Build guard for missing bundle

The build SHALL handle a missing `src/dendrite/ui/dist/index.html` in an sdist or wheel build as follows:

- The build SHALL first try to build it with `npm`, when `ui/` and `npm` are available.
- If it is still missing, a release build (`DENDRITE_REQUIRE_UI=1`) SHALL fail.
- Any other build SHALL package the API without the UI and say so.

Editable installs, and builds run with `DENDRITE_SKIP_UI_CHECK=1`, never build the UI.

#### Scenario: Missing bundle blocks release build

- **WHEN** `DENDRITE_REQUIRE_UI=1 uv build --wheel` is run without a built UI, and npm can't build it
- **THEN** the build fails with a message telling the user to run `scripts/build-ui.sh`

#### Scenario: A git install without Node.js still installs

- **WHEN** dendrite is installed from git on a machine without npm
- **THEN** the install succeeds without the UI, and pipelines, the CLI and hosted mode work

#### Scenario: Editable install unaffected

- **WHEN** `uv sync` is run without a built UI
- **THEN** the install succeeds

### Requirement: Serve the bundle, or the API with a build hint

The server SHALL serve the React bundle when it is present. Otherwise it SHALL serve the API alone, with a short page at `/` explaining how to build the UI. `/api/info` SHALL report which applies.

#### Scenario: Bundle present

- **WHEN** `dist/index.html` exists and `/` is requested
- **THEN** the React app is returned, its assets are served from the bundle, and `/api/info` reports `ui: "react"`

#### Scenario: Bundle absent

- **WHEN** `dist/` does not exist and `/` is requested
- **THEN** a page explaining how to build the UI is returned, the API works, and `/api/info` reports `ui: "missing"`

### Requirement: Development server

The repository SHALL provide a Vite dev server with hot reload. It SHALL proxy `/api` to a running `dendrite ui` backend (default `http://127.0.0.1:8454`).

#### Scenario: Dev loop

- **WHEN** `dendrite ui` is running and `npm run dev` is started in `ui/`
- **THEN** the dev UI loads experiments through the proxy, and source edits hot-reload without restarting the backend
