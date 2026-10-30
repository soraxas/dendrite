## 1. Client configuration (local behaviour unchanged)

- [ ] 1.1 Add `config.resolve()` with the precedence from design decision 5; `Lab()` uses it, and every existing direct and local path behaves as before
- [ ] 1.2 Read `dendrite.toml` (searching upward) and add `dendrite init --server --project`
- [ ] 1.3 Tests: resolution order, environment overriding the file, no-config local mode

## 2. Remote metadata and the test harness

- [ ] 2.1 Add the remote allowlist registry in `db.py` (method → role, plus a caller check), covering the methods clients use
- [ ] 2.2 Add the wire codec (`ArtifactRef`, bytes, tagged floats so `1` and `1.0` stay distinct) and the `rpc/{method}` and `rpc/batch` routes for one project; single project, API tokens only
- [ ] 2.3 Add `HttpMetadata` (keep-alive `httpx`, retries, idempotency keys, `X-Dendrite-Version`)
- [ ] 2.4 Add the `DENDRITE_TEST_REMOTE=1` fixture: in-process server over the ASGI transport, a project schema on the test Postgres, moto for blobs, a service token
- [ ] 2.5 Make the existing suite pass under `DENDRITE_TEST_REMOTE=1`; add it to the CI matrix

## 3. Brokered blobs

- [ ] 3.1 Server: `blobs/missing`, `blobs/presence`, `blobs/urls`, all per-project prefix and batched at 1000
- [ ] 3.2 Upload plans per store: conditional `If-None-Match: *` PUTs; SHA-256 on S3/MinIO (whole object, or per part for multipart); CRC32C on GCS resumable/multipart; `blobs/commit` checks size and stored checksum
- [ ] 3.3 `verify_uploads = "hash"` and `store_enforces_sha256`: staging keys, server-side SHA-256 then copy into place, 202 and polling, GC of stale staging keys
- [ ] 3.4 Add `ServerStore` (upload only what's missing, presigned GET into the checkout cache, batched `verify`)
- [ ] 3.5 Publish refuses uncommitted outputs; record `value_previews` at publish time, and never unpickle on the server
- [ ] 3.6 Tests: mismatched bytes rejected (`verify_uploads = "hash"`), no overwrite, a large file via the staged path, presence batching counts

## 4. Permissions and attribution

- [ ] 4.0 Route cache lookups through `cache_scopes(project)`, which returns only the project itself for now
- [ ] 4.1 Control schema: users, projects, memberships, tokens (stored hashed), sessions, device logins
- [ ] 4.2 Caller checks: experiment ownership for node-run, stage, progress and read writes; leases bound to the token; publish requires the lease
- [ ] 4.3 Roles: viewer/runner/admin gates; owner-only delete for runners
- [ ] 4.4 Attribution side tables for experiments and executions; include them in API responses
- [ ] 4.5 Storage administration runs on the server per project (inventory, evict, gc, clean) behind `admin`
- [ ] 4.6 Tests: one test per role per write family, cross-project isolation, publishing without the lease, unlisted method returns 404

## 5. Login

- [ ] 5.1 OIDC authorization-code login on the server (authlib), with GitHub, Google and generic issuers; admin emails and auto-join domains
- [ ] 5.2 Device flow: `/auth/device`, the `/device` confirmation page, `/auth/token` polling and expiry
- [ ] 5.3 `dendrite login/logout/whoami`; the credentials file (0600, keyed by server); `DENDRITE_TOKEN`
- [ ] 5.4 Automatic prompt on the first server call (TTY and Jupyter detection) and `NotLoggedIn` elsewhere; `Lab()` never prompts
- [ ] 5.5 `dendrite token create/list/revoke` for scoped service tokens
- [ ] 5.6 Tests with a fake OIDC issuer: login, expired code, logout revokes, CI fails fast, scoped token returns 403 in other projects

## 6. Round trips at scale

- [ ] 6.1 Coalesce progress and heartbeats per process into batched calls every 2 s
- [ ] 6.2 Measure the 300-item fan-out remotely against locally; aim for under 2× on a LAN, and record the numbers in the design

## 7. Hosted UI

- [ ] 7.1 Session cookie, CSRF header and a sign-in gate; a `/api/v1/session` endpoint
- [ ] 7.2 Project switcher, and "run by" on experiments and executions
- [ ] 7.3 Role-aware controls; source editing off in hosted mode; value previews from recorded text
- [ ] 7.4 UI tests for the gate, role-hidden controls and attribution display

## 8. Deployment and docs

- [ ] 8.1 `dendrite server` config loading, the Postgres-only check, and `min_client` version negotiation (426)
- [ ] 8.2 `dendrite server adopt` for an existing direct-mode schema and prefix
- [ ] 8.3 Docker image and an example `server.toml`; document the network setup (Postgres and bucket private to the server)
- [ ] 8.4 README "Team setup" section; CLAUDE.md architecture and invariants (server never unpickles, uploads committed before publish)
