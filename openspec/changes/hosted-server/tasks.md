## 1. Client configuration (local behaviour unchanged)

- [x] 1.1 Add `config.resolve()` with the precedence from design decision 5; `Lab()` uses it, and every existing direct and local path behaves as before
- [x] 1.2 Read `dendrite.toml` (searching upward) and add `dendrite init --server --project`
- [x] 1.3 Tests: resolution order, environment overriding the file, no-config local mode

## 2. Remote metadata and the test harness

- [x] 2.1 Add the remote allowlist registry in `db.py` (method → role, plus a caller check), covering the methods clients use (in `hosted/wire.py`, not `db.py`)
- [x] 2.2 Add the wire codec (`ArtifactRef`, bytes, tagged floats so `1` and `1.0` stay distinct) and the `rpc/{method}` and `rpc/batch` routes for one project; single project, API tokens only (no float tagging needed: Python's `json` keeps `1` and `1.0` apart)
- [x] 2.3 Add `HttpMetadata` (keep-alive `httpx`, retries, idempotency keys, `X-Dendrite-Version`)
- [x] 2.4 Add the `DENDRITE_TEST_REMOTE=1` fixture: in-process server over the ASGI transport, a project schema on the test Postgres, moto for blobs, a service token (a real uvicorn server in a thread rather than the ASGI transport)
- [x] 2.5 Make the existing suite pass under `DENDRITE_TEST_REMOTE=1`; add it to the CI matrix

## 3. Brokered blobs

- [x] 3.1 Server: `blobs/missing`, `blobs/presence`, `blobs/urls`, all per-project prefix and batched at 1000
- [ ] 3.2 (S3/MinIO done; GCS signed uploads not yet, GCS is proxied through the server) Upload plans per store: conditional `If-None-Match: *` PUTs; SHA-256 on S3/MinIO (whole object, or per part for multipart); CRC32C on GCS resumable/multipart; `blobs/commit` checks size and stored checksum
- [x] 3.3 `verify_uploads = "hash"` and `store_enforces_sha256`: staging keys, server-side SHA-256 then copy into place, 202 and polling, GC of stale staging keys
- [x] 3.4 Add `ServerStore` (upload only what's missing, presigned GET into the checkout cache, batched `verify`)
- [x] 3.5 Publish refuses uncommitted outputs; record `value_previews` at publish time, and never unpickle on the server
- [x] 3.6 Tests: mismatched bytes rejected (`verify_uploads = "hash"`), no overwrite, a large file via the staged path, presence batching counts

## 4. Permissions and attribution

- [x] 4.0 Route cache lookups through `cache_scopes(project)`, which returns only the project itself for now
- [x] 4.1 Control schema: users, projects, memberships, tokens (stored hashed), sessions, device logins
- [x] 4.2 Caller checks: experiment ownership for node-run, stage, progress and read writes; leases bound to the token; publish requires the lease
- [x] 4.3 Roles: viewer/runner/admin gates; owner-only delete for runners
- [x] 4.4 Attribution side tables for experiments and executions; include them in API responses
- [x] 4.5 Storage administration runs on the server per project (inventory, evict, gc, clean) behind `admin`
- [x] 4.6 Tests: one test per role per write family, cross-project isolation, publishing without the lease, unlisted method returns 404

## 5. Login

- [x] 5.1 (httpx, not authlib; GitHub path untested against GitHub) OIDC authorization-code login on the server (authlib), with GitHub, Google and generic issuers; admin emails and auto-join domains
- [x] 5.2 Device flow: `/auth/device`, the `/device` confirmation page, `/auth/token` polling and expiry
- [x] 5.3 `dendrite login/logout/whoami`; the credentials file (0600, keyed by server); `DENDRITE_TOKEN`
- [x] 5.4 Automatic prompt on the first server call (TTY and Jupyter detection) and `NotLoggedIn` elsewhere; `Lab()` never prompts
- [x] 5.5 `dendrite token create/list/revoke` for scoped service tokens
- [x] 5.6 Tests with a fake OIDC issuer: login, expired code, logout revokes, CI fails fast, scoped token returns 403 in other projects (plus GitHub, which is untested against real GitHub)

## 6. Round trips at scale

- [ ] 6.1 (progress and queued node runs are batched; lease heartbeats are still one request each) Coalesce progress and heartbeats per process into batched calls every 2 s
- [x] 6.2 Measure the 300-item fan-out remotely against locally; aim for under 2× on a LAN, and record the numbers in the design

## 7. Hosted UI

- [x] 7.1 Session cookie, CSRF header and a sign-in gate; a `/api/v1/session` endpoint
- [x] 7.2 (the switcher is the server's project list) Project switcher, and "run by" on experiments and executions
- [x] 7.3 Role-aware controls; source editing off in hosted mode; value previews from recorded text
- [ ] 7.4 (the gate is tested in Python; HostedBar and CSRF in Vitest; role-hidden controls not yet) UI tests for the gate, role-hidden controls and attribution display

## 8. Deployment and docs

- [x] 8.1 `dendrite server` config loading, the Postgres-only check, and `min_client` version negotiation (426)
- [x] 8.2 `dendrite server adopt` for an existing direct-mode schema and prefix
- [x] 8.3 (the image installs Prefect 3.8.8 from PyPI; docs/deploying.md; `dendrite server check`) Docker image and an example `server.toml`; document the network setup (Postgres and bucket private to the server)
- [x] 8.4 README "Team setup" section; CLAUDE.md architecture and invariants (server never unpickles, uploads committed before publish)
