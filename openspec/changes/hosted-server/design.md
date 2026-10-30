## Context

dendrite runs pipelines in-process. Each `Lab` holds a `Metadata` (SQLAlchemy, SQLite or Postgres) and an `ArtifactStore` (local or fsspec), and every runner talks to both directly. Sharing therefore means handing everyone database and bucket credentials and network reach to both.

Two facts make a hosted mode tractable:

- **`Metadata` is already the boundary.** Runs, tasks, storage and sources reach the database through `Metadata` methods. Clients call about 27 distinct methods, and only three places outside `db.py` open a connection directly.
- **Signatures are location-independent.** They cover source text, input hashes, consumed params, the Python version, `DENDRITE_ENV` and declared packages. They don't depend on file paths or users, so two people's identical work already gets the same signature.

Constraints:

- Local solo use must not change.
- Every core invariant still holds: atomic publish, compare-and-set draft transitions, leases with heartbeats, verified cache hits, and side-effect-free plans.
- The schema has no migrations (`create_all`), so core tables can't gain columns.

## Goals / Non-Goals

**Goals:**

- A teammate clones a repo, runs the pipeline, logs in once when prompted, and then reuses everything the team already computed.
- Postgres and the bucket are reachable only by the server.
- Every write is permission-checked and attributed to a user.
- The existing test suite proves the remote mode behaves like the local one.

**Non-Goals:**

- remote execution or workers
- replacing pickles
- multi-region servers
- offline queues
- fine-grained permissions below the project level

## Decisions

### 1. One server process; projects are Postgres schemas

`dendrite server --config server.toml` serves the per-project API, the auth endpoints and the web UI.

**How projects are stored:**

- Each project is its own Postgres schema holding the unchanged dendrite tables. The server keeps one `Metadata` per project, which the test suite's `pg_schema_url` already exercises.
- A separate `dendrite_control` schema holds:
  - `users` (OIDC issuer and subject, email, name)
  - `projects`
  - `memberships` (user, project, role)
  - `tokens` (hashed, scoped to a project and role, with expiry and revocation)
  - `device_logins`
  - `sessions`

**Why:**

- Projects are isolated: cache scope, GC reachability and deletes stay within a project.
- The core schema doesn't change, so an existing direct-Postgres deployment can be adopted as a project without migrating data.

**Requirements and alternatives:**

- The server requires Postgres. SQLite is only for local mode.
- *Alternative:* a `project_id` column on every core table. Rejected because it needs migrations, and every query would have to carry the filter.

**Cache scopes: the extension point for sharing between projects.**

- Every cache lookup (`find_success`, and `verify` of the hit's outputs) goes through `cache_scopes(project)`. That is an ordered list of `(schema, blob prefix)` pairs to search.
- In this change the list is always just `[project]`.
- A later change can add *cache grants*. Project `splats` could then also read `datasets`' cache: lookups would search `datasets` after `splats` itself, and presigned GETs would be allowed in `datasets`' prefix for granted hashes. Writes always stay in the caller's own project.
- Nothing else needs to change: signatures are already project-independent, and a hit only needs a readable execution plus verified blobs.
- GC of a granted project would also have to treat the grantee's node runs as roots. That's part of the follow-up, not this change.

### 2. Blobs: one prefix per project, and the server holds the credentials

Objects live at `<store>/<project>/objects/<h[:2]>/<h[2:]>`. The server signs every URL.

**Prefix per project:**

- A project member can only obtain URLs inside their project's prefix.
- Knowing or guessing a hash from another project yields nothing.
- Deduplication across projects is lost. That's accepted, because isolation matters more.

**Downloads:**

- A presigned GET, signed per request, and the server logs each signing (user, hash, time).
- It expires after 15 minutes, a deliberate choice: S3 and GCS allow up to 7 days.
- The client fetches into the same local checkout cache that `FsspecStore` uses.

**Signing credentials:**

- S3 and MinIO sign with the server's access key.
- On GCP without a key file (GKE, Cloud Run), the server's service account needs `iam.serviceAccounts.signBlob` on itself.

**Uploads, and when a blob counts as present.** Deduplication comes first and applies at every size: an existing blob is never uploaded again.

1. **Hash locally.** The client hashes the file as it does today.

2. **Ask which blobs are missing.** `POST /blobs/missing` takes up to 1000 hashes and sizes per call. Hashes the project already has are skipped. For the rest, the server returns an upload plan (step 3).

3. **Upload**, straight to the bucket. Every upload is conditional (`If-None-Match: *`), so an existing object is never overwritten. The store checks whatever checksum it supports while receiving the bytes:

   | Store | Single request | Large file |
   |---|---|---|
   | S3, MinIO | SHA-256 of the whole object (up to 5 GB per PUT) | multipart, SHA-256 per part |
   | GCS | CRC32C (or MD5) of the whole object | resumable or multipart, CRC32C |

   Only S3 single-part uploads prove the blob's *identity*, because dendrite's hash is a SHA-256 of the bytes. The other checks catch corruption, not a client that sends different bytes on purpose.

4. **Commit.** `POST /blobs/commit` confirms the object exists with the declared size, and with the stored checksum where the store keeps one.

5. **Publish only after commit.** `publish` refuses outputs that haven't been committed.

**How strictly to verify** is a server setting, `verify_uploads`:

- **`checksum`** (default): rely on the store's checks above.
  - This catches accidental corruption at no extra cost.
  - It trusts a runner not to upload different bytes on purpose. That's the same trust as relying on runners to compute honest results (see Risks).
- **`hash`**: every upload whose identity the store hasn't already proven goes to a staging key `<project>/uploads/<id>`.
  - On commit the server streams the object, checks its SHA-256, and copies it into place server-side.
  - It answers `202` while that runs, and the client polls.
  - This costs one server read per *new* blob. Re-used blobs cost nothing.
  - S3 single-part uploads skip staging, because the store already enforced the SHA-256. `store_enforces_sha256 = false` turns that off, for stores that accept the header but don't check it.
- Uncommitted staging keys older than a day are removed by GC.

**Testing note:** moto accepts a presigned PUT whose bytes don't match its checksum, though it does honour `If-None-Match`. Tests therefore run with `verify_uploads = "hash"` and `store_enforces_sha256 = false`, which proves the server rejects a mismatched upload.

**Alternative:** proxying blobs through the server. Rejected because the server would carry all traffic for multi-GB outputs.

### 3. Remote calls: an explicit allowlist with a semantic check for each write

`Metadata` methods are exposed only through an explicit registry: `REMOTE = {name: Rule(role, check)}`. A method missing from the registry can't be called remotely.

**Reads** need `viewer`:

- `experiment`, `experiments`, `node_runs`, `execution`, `find_success`
- `ancestor_executions` / `descendant_executions`, `artifact`, `artifacts_by_hash`, `resolve_artifact_prefix`
- `producer_of`, `metrics_for`, `experiments_using_artifact`, `change_tokens`, `executions_brief`

**Writes** need `runner`, plus a check bound to the caller:

- **`create_experiment` and `update_experiment_if`:** the server records the caller as the owner. The draft-to-running compare-and-set is kept.
- **`upsert_node_run`, `set_node_stage`, `set_progress`, `record_read`:** the experiment must be owned by the caller, or by a runner token issued for that experiment's run.
- **`acquire_lease`, `renew_lease`, `release_lease`:**
  - The lease's worker id is bound to the caller's token.
  - You can only renew or release your own lease.
  - Expiry still lets someone else take over a lease whose holder crashed.
- **`publish`:** the caller must hold the lease for that signature, and every output must be committed (decision 2). The call runs in one transaction on the server, as it does locally.
- **`register_artifacts`:** limited to hashes the caller committed, or that already exist in the project.

**Admin operations** need `admin`:

- `delete_experiments`, `set_keep`, `mark_stopped`, evict, GC and clean
- These run **on the server**, through the existing `storage.py`, against the server's own store. A client holding only presigned URLs can't list the bucket.

**Owners:** deleting your own experiment needs only `runner`. Deleting anyone else's needs `admin`.

**Wire format:**

- `POST /api/v1/projects/{p}/rpc/{method}` with JSON `{args, kwargs}`.
- A small codec covers `ArtifactRef`, bytes and floats. `1` and `1.0` must stay distinct, so floats travel tagged.
- *Alternative:* hand-written REST resources for each operation. Rejected: two surfaces to keep in sync. The UI's existing REST endpoints stay as they are, served per project.

### 4. Login: the dendrite server runs the device flow itself

Not every OIDC issuer supports device authorization (RFC 8628), and Google restricts it. So the CLI talks only to the dendrite server:

1. `dendrite login` calls `POST /auth/device`. The server answers with a `device_code`, a `user_code` and `verification_uri` = `https://server/device`.
2. The CLI prints the URL and code, opens the browser if it can, and polls `POST /auth/token`.
3. In the browser, the user signs in with an ordinary OIDC authorization-code login (GitHub, Google or a generic issuer, set in the server config). They then confirm the code on the server's page.
4. The server mints a **dendrite token**:
   - opaque, stored only as a hash
   - expires after 90 days, renewed on use
   - revocable
   - scoped to the user, usable in every project they belong to

**Tokens on the client:**

- Stored in `~/.config/dendrite/credentials.toml`, mode 0600, keyed by server URL.
- `DENDRITE_TOKEN` overrides that file, for CI and service accounts.
- `dendrite token create --project P --role runner --name ci` issues a scoped service token. Admin only.

**Web UI:**

- The same OIDC login, with a session cookie: `HttpOnly`, `Secure`, `SameSite=Lax`.
- Mutating requests also require an `X-Dendrite-CSRF` header taken from `/api/v1/session`.

**First user:** the server config can name `admins` (emails), and an optional `allowed_domains` rule auto-joins new users to projects as viewers.

### 5. Client configuration, and when to prompt for login

`config.resolve()` applies the first match, highest precedence first:

1. explicit `Lab(db_url=…, store_url=…)` or `Lab(server=…)`
2. `DENDRITE_DB_URL` / `DENDRITE_STORE_URL`, which select direct mode
3. `DENDRITE_SERVER` + `DENDRITE_PROJECT`
4. the nearest `dendrite.toml`, searching upward from the current directory: `[server] url`, `project`
5. otherwise local mode, exactly as today

`dendrite.toml` never contains credentials, so it's safe to commit. `dendrite init` writes it.

**Prompting:** `Lab()` doesn't prompt. It may be constructed at import time, inside the server, or in threads. The prompt happens on the first remote call that needs a token, which is normally `run()`, `plan()` or the first read:

- **In a terminal**, or an IPython/Jupyter kernel: it prints `Log in to https://lineage.team (project recon)` with the code and URL, and waits.
- **Anywhere else:** it raises `NotLoggedIn("run `dendrite login` or set DENDRITE_TOKEN")`.

Showing the server URL matters, because the repo's `dendrite.toml` chose it.

### 6. Cutting down round trips

A 1000-item fan-out locally makes thousands of cheap SQL calls. Remotely, each call is an HTTPS round trip. Mitigations:

- **One connection:** a single keep-alive `httpx.Client` per process, using HTTP/2 when available.
- **Batched endpoints:** `blobs/missing`, `blobs/presence` (for `verify` of directory manifests), `blobs/urls` (presigned GETs in bulk) and `rpc/batch` (a list of calls run in one transaction where it's safe).
- **Coalesced progress:** progress and heartbeats are merged per process and sent at most every 2 s as a batch. The local throttle stays at `PROGRESS_INTERVAL`.
- **Retries:** reads retry with backoff. Writes carry an idempotency key, so a retried `publish` or `upsert_node_run` takes effect once.

### 7. Proving "behaves like local": run the existing suite remotely

`DENDRITE_TEST_REMOTE=1` switches the shared `lab` fixture to a `Lab(server=…)`. Behind it are:

- an in-process server, using httpx's ASGI transport, so no ports are needed
- a project schema on the test Postgres
- moto for blobs
- a fake OIDC issuer

The whole existing suite, plus new auth and permission tests, runs against it. This comes early in the task plan, before any UI work, the same way `DENDRITE_TEST_DB=postgres` was introduced.

### 8. The UI in hosted mode

- The same React app.
- A sign-in gate and a project switcher.
- An owner column and "run by" on experiments.
- Controls hidden according to role.
- Source editing off: the server can't write a user's files.
- Draft plans answer 409 `pipeline_not_imported`, as they already do without `--import`, because the server doesn't have users' pipeline code. Users plan locally with `Experiment.plan()`.
- **The server never unpickles.** Locally the UI previews a value artifact by unpickling it. On a hosted server that would let any runner run code on the server.
  - Instead, the producing client records a truncated `repr` at publish time, in a `value_previews` side table. It already trusts its own value.
  - The UI shows that text. Values without one show only their size and a download link.

## Implementation notes

What building it changed or added:

- **Small blobs travel inline.** Blobs of at most 256 KB go up inside the `blobs/missing` request, and the server hashes and stores them. Fan-outs of small values would otherwise pay three round trips each.

- **Stores that can't presign are proxied.** A local directory, `memory://` and, for now, GCS stream through the server, which hashes them on the way in. GCS signed uploads (CRC32C) are a follow-up.

- **Upload sessions and idempotency keys live in the server process.** One replica, or sticky sessions, until they move to Postgres.

- **The server caches authentication for 30 s.** Token and role lookups are reused (`AUTH_CACHE_SECONDS`), and `last_used_at` is written at most once a minute. Revoking on the same server takes effect at once.

- **Connection lifetimes.** Clients drop idle connections after 2 s, below the server's 30 s keep-alive. With equal lifetimes, a request sent on a connection the server was closing was lost and waited out the client's 120 s timeout.

- **Two latent bugs, also present in direct mode:**

  - `FsspecStore.has` used `fs.exists`, which s3fs answers from cached directory listings, so a blob uploaded after a listing looked missing.
  - Concurrent inserts of the same source snapshot or node run could collide on Postgres. Both now insert inside a savepoint.

- **Measured.** A 300-step fan-out on one machine (MinIO, Postgres, server and client local), timing the first run and a fully cached re-run:

  | | first run | cached re-run |
  |---|---|---|
  | local SQLite | 5.5–7.1 s | 3.9–4.0 s |
  | hosted | 8.8–10.0 s | 6.1–9.0 s |

  That is about 1.4–1.6× for the first run and 1.5–2.3× cached.

## Risks / Trade-offs

- **A runner could publish a wrong result under a valid signature** and poison the project cache. → Runners are trusted, as today. Attribution makes this traceable, and admins can evict by user. Documented in the security notes.
- **Pickled value artifacts run code when loaded**, including other users' values. → Documented. A safer value format for common types is the follow-up. The server itself never unpickles: previews come from producer-recorded `repr` text (decision 8).
- **Latency on very chatty pipelines.** → Batching (decision 6). Measured on the 300-item fan-out from the live-graph work. The target is under 2× the local wall time on a LAN.
- **Verifying large staged uploads costs server bandwidth**: hashing streams the whole object once. → Only above 5 GB, or on stores without a checksum. It runs asynchronously, and publish waits for it.
- **The server is a single point of failure for runs.** → Runs fail fast with a clear error. No offline queue (a non-goal). The server is stateless apart from Postgres, so running several replicas behind a load balancer works.
- **Client and server versions drift.** → Every request carries `X-Dendrite-Version`. The server rejects clients older than its `min_client`, with an upgrade message. The API stays additive within `/api/v1`.

## Migration Plan

1. **Nothing changes until a server is configured.** Local and direct modes are unchanged.
2. **Adopting a direct-Postgres deployment:** `dendrite server adopt --schema public --prefix s3://bucket/prefix --project NAME` registers the existing tables and bucket prefix as a project. Lineage history carries over; blobs stay where they are.
3. **Rollback:** point clients back at `DENDRITE_DB_URL` / `DENDRITE_STORE_URL` for that schema and prefix. The data is the same tables.

## Open Questions

- **Default for `verify_uploads`:** `checksum` is free and matches the existing trust in runners. Teams that don't fully trust every runner should pick `hash`. Should the default instead depend on the store, for example `hash` on GCS?
