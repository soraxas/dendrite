# A team server on one machine

This walks through hosted mode end to end. A dendrite server owns Postgres and a bucket, people sign in, and anything one person computed, everyone else in the project reuses.

```
your laptop ──HTTPS──► dendrite server ──► Postgres   (lineage, users, tokens)
     │                       └───────────► bucket     (signs URLs; never proxies bytes)
     └──── presigned URLs ───────────────► bucket     (uploads and downloads go direct)
```

What a server needs:

- **The dendrite server** (`dendrite server run`), a FastAPI app. It serves the API and every project's web UI.
- **Postgres.** Only the server connects to it.
- **A bucket**: S3, MinIO or anything S3-compatible. The server signs URLs; browsers and clients transfer bytes directly. A plain directory also works, in which case bytes stream through the server.
- **An identity provider for sign-in**: GitHub or any OpenID Connect issuer. This example uses `dev`, which signs you in by email and only runs on localhost.

## 1. Start Postgres and MinIO

```bash
cd examples/team
docker compose up -d --wait db minio && docker compose run --rm bucket
```

MinIO stopped publishing community images after `RELEASE.2025-09-07T16-13-09Z`, which is the release pinned here. Any S3-compatible store works: change `store_url` and `[store_options]` in `server.toml`.

## 2. Run the server

```bash
uv sync --extra server --extra s3
uv run dendrite server run --config server.toml          # http://127.0.0.1:8455
```

In another terminal, make yourself an admin, then create a project and add people to it:

```bash
export DENDRITE_TOKEN=$(uv run dendrite server admin-token --config server.toml --email admin@example.com)
uv run dendrite project create demo
uv run dendrite member add demo alice@example.com runner
uv run dendrite member add demo bob@example.com runner
unset DENDRITE_TOKEN
```

People you add don't need to have signed in before. Their first sign-in with that email claims the membership.

## 3. Run the pipeline

`dendrite.toml` in this directory names the server and project. `pipeline.py` doesn't mention either:

```bash
uv run python pipeline.py
```

```
Sign in to http://127.0.0.1:8455 (project demo)
  open    http://127.0.0.1:8455/device?code=RNQD-VNNS
  code    RNQD-VNNS
```

Open the link, sign in as `alice@example.com`, and approve. The run then continues:

```
top-3: {'count_words': 'computed', 'top': 'computed'}
```

The token is saved in `~/.config/dendrite/credentials.toml` (mode 600), so later runs don't ask again. `dendrite login`, `logout` and `whoami` manage it.

## 4. Someone else reuses it

As Bob, on another machine or with a separate config and home:

```bash
export XDG_CONFIG_HOME=/tmp/bob DENDRITE_HOME=/tmp/bob/home
uv run python pipeline.py            # sign in as bob@example.com
#  top-3: {'count_words': 'cached', 'top': 'cached'}      nothing recomputed
uv run python pipeline.py --n 5
#  top-5: {'count_words': 'cached', 'top': 'computed'}    only the changed step runs
```

Bob's machine downloads Alice's results straight from MinIO, and only when a step reads them.

## 5. Look at it

Open `http://127.0.0.1:8455`, sign in, and pick `demo`. It's the usual dendrite UI, with each experiment showing who ran it:

- **Viewers** browse.
- **Runners** edit drafts and stages.
- **Admins** also reclaim storage.

## CI

Give CI a token scoped to one project and role, instead of a person's sign-in:

```bash
uv run dendrite token create --project demo --role runner --name ci     # prints the token once
```

Set it as `DENDRITE_TOKEN` in CI. Without a token, a non-interactive run fails immediately with `NotLoggedIn` instead of waiting for a sign-in.

## The server as a container

The repo's `Dockerfile` builds the server image, and Compose can run it next to the same Postgres:

```bash
docker compose --profile server up -d --wait server        # http://127.0.0.1:8456, config: server.docker.toml
```

That variant keeps blobs in a volume and streams them through the server, because presigned URLs must name a hostname that both the server and your machine can reach, which an in-Compose MinIO doesn't have. With a real S3 bucket, clients transfer directly as above.

## For a real deployment

See [`docs/deploying.md`](../../docs/deploying.md). In short:

- **TLS:** put the server behind HTTPS and set `base_url` to that address.
- **Sign-in:** use `provider = "github"`, or `"oidc"` with an `issuer`, and pass `client_secret = "${GITHUB_CLIENT_SECRET}"` through the environment.
- **Network:** keep Postgres private to the server. The bucket only needs to be reachable for presigned URLs.
- **Upload checks:** `verify_uploads = "checksum"` relies on the store to check each upload's SHA-256, as S3 and MinIO do. `"hash"` also has the server re-hash uploads the store couldn't verify, such as multipart uploads.
- **Existing data:** `dendrite server adopt --schema … --store … --project …` turns an existing direct-mode database schema and bucket prefix into a project, without copying anything.

To clean up after this example: `docker compose down -v` removes the containers and their data.
