## Why

Sharing results today means giving every researcher direct credentials to the team's Postgres and S3 bucket, and keeping both reachable from every laptop and GPU box. That's unsafe and awkward to set up, and there's no notion of who ran what or who may delete what. The goal is the experience of `gh` or `wandb`: wrap a pipeline, run it, log in when asked, and from then on results are tracked, uploaded and reused across the whole team.

## What Changes

- **A self-hostable dendrite server** (`dendrite server`). It owns the Postgres database and the bucket, and nothing else needs to reach them. It serves a versioned JSON API (`/api/v1`) and the existing web UI.
- **Projects.** Each project has its own lineage, cache and blob prefix. Cache reuse happens across every member of a project, and never across projects.
- **Roles.**
  - `viewer` browses and downloads.
  - `runner` also runs experiments and uploads results.
  - `admin` also deletes, evicts, runs GC and manages members and tokens.
- **Run attribution.** Experiments and executions record the user who created them, and the UI shows it.
- **Login with an existing identity provider.** The server delegates sign-in to OpenID Connect: GitHub, Google or any OIDC issuer. It never stores passwords.
  - **The CLI** signs in with the OAuth device flow (`dendrite login`): open a URL, confirm a code.
  - **The web UI** uses a normal browser sign-in with a session cookie.
  - **CI and service accounts** use revocable API tokens (`DENDRITE_TOKEN`) that admins issue per project and role.
- **Project config in the repo.** `dendrite init --server URL --project NAME` writes a `dendrite.toml`. The repo then carries the team's server, so a teammate who clones it and runs the pipeline is asked to log in, and nothing else.
- **Automatic login prompt.** When a project is configured for a server but there are no credentials:
  - in a terminal or notebook, `Lab()` starts the device-flow login inline
  - non-interactively, it fails with `NotLoggedIn` and says how to fix it
- **A remote client that looks like the local one.**
  - `HttpMetadata` implements the existing `Metadata` interface over the API.
  - `ServerStore` implements `ArtifactStore` with presigned URLs, so uploads and downloads go straight to the bucket, never through the server.
  - Pipelines, steps, signatures, cache verification, forks, pins and plans behave as they do locally.
- **Upload only what's new, and check what's uploaded.**
  - **Deduplication at every size:** before uploading, the client asks which hashes the project lacks. Existing blobs are never sent again.
  - **Store-enforced checksums:** new uploads carry the strongest checksum the store enforces. That's SHA-256 on S3 and MinIO, and CRC32C on GCS.
  - **No overwrites:** uploads are conditional, so an existing object is never overwritten.
  - **Commit before use:** a blob is usable only after the server commits it, and no publish can reference an uncommitted blob.
  - **Stricter mode:** `verify_uploads = "hash"` also has the server re-hash every new blob whose SHA-256 the store didn't enforce. That covers GCS and S3 multipart.
- **No change for solo use.** With no `dendrite.toml` and no server configured, `Lab()` uses local SQLite and a local store exactly as now. Explicit `db_url`/`store_url` still work too.

Non-goals:

- remote execution or workers: runs still execute on the machine that starts them
- replacing pickled value artifacts
- multi-region or federated servers
- an offline queue for runs made while the server is unreachable

## Capabilities

### New Capabilities

- `hosted-server`: the server process, its configuration, projects (with cache scopes as the extension point for sharing between projects), roles, run attribution and the versioned API that wraps metadata operations with permission checks.
- `client-login`: project config (`dendrite.toml`), `dendrite init`/`login`/`logout`/`whoami`, the device flow, stored credentials, API tokens, and the automatic login prompt.
- `remote-artifacts`: brokered blob access through presigned upload and download URLs, the upload checksum, and batched presence checks.

### Modified Capabilities

- `metadata-store`: metadata can come from a remote server as well as a SQLAlchemy URL, with the same behaviour, including atomic publish.
- `artifact-store`: adds a server-brokered backend alongside the local and fsspec ones.
- `lineage-ui`: in hosted mode the UI requires sign-in, shows who ran each experiment, hides actions the user's role doesn't allow, and turns off source editing.

## Impact

- **New code:**
  - `src/dendrite/server_app/`: control-plane tables, auth, the permission layer and API routes
  - `src/dendrite/remote.py`: `HttpMetadata` and `ServerStore`
  - `src/dendrite/config.py`: resolving `dendrite.toml`, environment variables and stored credentials
- **Changed code:**
  - `experiment.py`: `Lab` resolves the server
  - `db.py`: marks which `Metadata` methods are remote-callable, and with which role
  - `cli.py`: `server`, `init`, `login`, `logout`, `whoami` and `token` commands
  - `server.py`: reused as the per-project API
  - UI: a sign-in gate, user display and role-aware controls
- **Database:**
  - Each project gets its own Postgres schema holding the unchanged dendrite tables, so the core schema doesn't change.
  - Attribution lives in new side tables, following the `experiment_flags` pattern.
  - A separate control schema holds users, projects, memberships, tokens and device-login sessions.
- **Dependencies:** `httpx` on the client; on the server, an OIDC library (`authlib`) and a Docker image for deployment.
- **Security:** Postgres and the bucket stay private to the server. Runners are still trusted to compute honest results, and pickled values still execute on load; both are documented, not solved.
