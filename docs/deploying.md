# Deploying a dendrite server

A dendrite server lets a team share one cache. It owns Postgres and the bucket; people sign in through your identity provider and never hold database or bucket credentials. To try it on one machine first, see [`examples/team`](../examples/team).

```
people, CI ──HTTPS──► dendrite server (1+ replicas) ──► Postgres (private)
     │                                └──────────────► bucket (signs URLs only)
     └────────── presigned URLs ──────────────────────► bucket
```

## 1. The image

```bash
docker build -t dendrite-server .     # from the repo root: the web UI is built into the image
docker run -p 8455:8455 -v $PWD/server.toml:/etc/dendrite/server.toml:ro dendrite-server
```

- The image runs `dendrite server run` on port 8455, as a non-root user.
- It reads its config from `/etc/dendrite/server.toml`, and keeps a small cache under `/var/lib/dendrite`.
- It never runs pipelines; clients do. It uses a released Prefect (see the `Dockerfile`).

Put it behind your usual HTTPS proxy or load balancer. Secrets go in environment variables, which `server.toml` reads as `${NAME}`.

## 2. `server.toml`

```toml
base_url = "https://lineage.example.com"          # exactly what browsers and clients use
database_url = "postgresql+psycopg://dendrite:${PGPASSWORD}@db.internal:5432/dendrite"
store_url = "s3://team-lineage/dendrite"          # each project gets <store_url>/<project>
home = "/var/lib/dendrite"
admins = ["you@example.com"]                       # admins everywhere, from their first sign-in
allowed_domains = ["example.com"]                  # optional: those people join every project as viewers
verify_uploads = "checksum"                        # S3 checks each upload's SHA-256 itself

[auth]
provider = "github"
client_id = "Iv1.…"
client_secret = "${GITHUB_CLIENT_SECRET}"
```

## 3. Sign-in

**GitHub.** Under Settings → Developer settings → OAuth Apps → New OAuth App:

- **Homepage URL:** your `base_url`.
- **Authorization callback URL:** `<base_url>/auth/callback`.
- **Credentials:** copy the client ID and a generated client secret into `[auth]`.

dendrite asks for `read:user user:email`, and uses only the account's primary email, and only if GitHub has verified it.

**Google, Okta, Keycloak, Entra ID or any OpenID Connect issuer.** Create an OAuth "web application" client with redirect URI `<base_url>/auth/callback`, then:

```toml
[auth]
provider = "oidc"
issuer = "https://accounts.google.com"     # where /.well-known/openid-configuration lives
client_id = "…"
client_secret = "${OIDC_CLIENT_SECRET}"
```

Sign-in uses PKCE. An email counts only when the issuer marks it verified: that's what lets an invitation by email (`dendrite member add`) attach to the right person.

## 4. The bucket

The bucket stays private. Clients receive short-lived URLs (15 minutes) for exactly the objects they may read or write, signed with the server's credentials.

- **Block all public access.** No bucket policy should grant anonymous access.

- **Give the server role access to `<store_url>/*` only:**

  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"],
        "Resource": "arn:aws:s3:::team-lineage/dendrite/*"
      },
      {
        "Effect": "Allow",
        "Action": "s3:ListBucket",
        "Resource": "arn:aws:s3:::team-lineage",
        "Condition": { "StringLike": { "s3:prefix": "dendrite/*" } }
      }
    ]
  }
  ```

  On EC2, ECS or EKS use an instance or task role. Elsewhere, set `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` for the server.

- **Clients must reach the bucket's endpoint**, because uploads and downloads go there directly. For MinIO or another S3-compatible store, set `[store_options] endpoint_url` to an address both the server and clients use: the signature covers the hostname.

- **No CORS rules are needed.** The CLI isn't a browser; and in the UI, downloads and video load directly, while the in-page file viewer streams through the server.

- **GCS works the same way:** signed V4 URLs, with `x-goog-if-generation-match: 0` so nothing is overwritten. On commit the server compares GCS's CRC32C with the client's. The server signs with its own service account through IAM `signBlob` (grant it `roles/iam.serviceAccountTokenCreator` on itself), or with a key file set as `gcs_signing_key`. [`deploy/gcp`](../deploy/gcp) sets all of this up on Cloud Run.

## 5. Postgres

- **Keep it private to the server**: same VPC or network, no public address.
- **Projects are schemas.** The server's role creates one per project, so give it its own database, or `CREATE` on one.
- **Connection budget.** Each open project holds at most 5 connections, plus about 15 for the control tables. With `max_open_projects = 16` (the default) that's about 95 per server process, so raise Postgres's `max_connections`, or lower `max_open_projects`, before adding replicas.

## 6. Replicas

Several replicas behind a load balancer work: upload sessions, sign-ins in progress and replies to retried writes all live in Postgres. Two things are cached per process for up to 30 seconds:

- **Token and role lookups.** A token revoked through one replica can keep working on another until its cached entry expires.
- **A verification job.** If a replica dies while hashing a large staged upload (`verify_uploads = "hash"`), another one takes over when the client next polls.

## 7. First run

```bash
dendrite server check --config server.toml          # database, bucket, checksums, sign-in config
dendrite server admin-token --config server.toml --email you@example.com   # once, to bootstrap
export DENDRITE_TOKEN=…                              # that token, for the next commands
dendrite project create recon --server https://lineage.example.com
dendrite member add recon alice@example.com runner --server https://lineage.example.com
unset DENDRITE_TOKEN
```

`dendrite server check` writes one small test object, checks that the bucket rejects bytes that don't match their hash and refuses overwrites, then deletes the object. If it reports that checksums aren't enforced, set `verify_uploads = "hash"` and `store_enforces_sha256 = false`, and the server will re-hash uploads itself.

Then, in a repo:

```bash
dendrite init --server https://lineage.example.com --project recon    # commit dendrite.toml
python run.py                                                          # signs in, then runs
```

For CI, use a scoped token instead of a person: `dendrite token create --project recon --role runner --name ci`.

## Existing data

A team already sharing one Postgres schema and bucket prefix directly (`DENDRITE_DB_URL`/`DENDRITE_STORE_URL`) can switch without moving anything:

```bash
dendrite server adopt --config server.toml --project recon --schema public --store s3://team-lineage/old-prefix
```
