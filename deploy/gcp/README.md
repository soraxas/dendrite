# dendrite on Google Cloud

The setup is a Cloud Run service, a Cloud SQL Postgres database and a private Cloud Storage bucket, with Google Workspace sign-in.

```
laptops ──HTTPS──► Cloud Run: dendrite server ──unix socket──► Cloud SQL (Postgres 17)
   │                     └── signs URLs with its service account
   └──── signed URLs ─────────────────────────────────────► Cloud Storage (private bucket)
```

Laptops never hold Google credentials. They sign in with their Workspace account, and upload and download straight to the bucket through short-lived signed URLs. Cloud Run's 32 MiB request limit doesn't apply, because file bytes don't pass through it.

`deploy.sh` does everything except two console steps, the OAuth client and DNS. It is safe to re-run: existing resources are kept, and the image and config are updated.

## 1. The OAuth client (console, once)

1. **Google Auth Platform → Branding.** Create the consent screen, with audience **Internal**, so only your Workspace can sign in.
2. **Clients → Create client → Web application.** Set the authorised redirect URI to `https://<your domain>/auth/callback`.
3. **Keep the credentials.** Copy the client ID and secret; `deploy.sh` stores the secret in Secret Manager.

## 2. Deploy

```bash
gcloud auth login
PROJECT=my-project REGION=australia-southeast1 DOMAIN=lineage.example.com \
ADMINS=you@example.com WORKSPACE_DOMAIN=example.com \
OAUTH_CLIENT_ID=….apps.googleusercontent.com OAUTH_CLIENT_SECRET=… \
./deploy/gcp/deploy.sh
```

It creates:

- **APIs:** enables the ones it needs.
- **A `dendrite-server` service account,** which may:
  - act as a Cloud SQL client
  - read its secrets
  - read and write objects in the bucket
  - sign URLs as itself: `roles/iam.serviceAccountTokenCreator`, used through IAM `signBlob`, so no key file
- **`gs://<project>-dendrite`:** private, with uniform access and public access prevention.
- **Cloud SQL `dendrite`:** Postgres 17 at `db-g1-small` with `max_connections=200`, plus a database and user whose generated password goes in Secret Manager.
- **Secrets:** the DB password, the OAuth secret, and the rendered `server.toml` (from `server.toml.tmpl`).
- **The image:** built by Cloud Build (linux/amd64, so it works from an Apple Silicon laptop) and stored in Artifact Registry.
- **The Cloud Run service:**
  - min 1 and max 2 instances; it does its own sign-in, so no Cloud Run auth layer
  - CPU always allocated, because large staged verifications run after the request returns
- **A `dendrite-check` job:** it runs `dendrite server check` with the same config at the end of every deploy. Its output (database, bucket, signing, sign-in) is in the job's logs.

Override any default with an environment variable: `SERVICE`, `BUCKET`, `SQL_INSTANCE`, `SQL_TIER`, `DB_NAME`, `DB_USER`, `TAG`.

## 3. The domain

Point your domain at the service in one of two ways:

- **Cloud Run domain mapping** (simplest, in supported regions):

  ```bash
  gcloud beta run domain-mappings create --service dendrite --domain lineage.example.com --region "$REGION"
  ```

  Then add the DNS records it prints. The certificate is issued automatically.

- **A global external Application Load Balancer** with a serverless NEG for the service, in regions without domain mappings, or for Cloud Armor or IAP in front.

`base_url` in the config must be exactly `https://<domain>`: sign-in redirects and signed URLs are built from it.

## 4. First sign-in and a project

The emails in `ADMINS` are server admins from their first sign-in:

```bash
dendrite login --server https://lineage.example.com      # approve in the browser with your Workspace account
dendrite project create recon --server https://lineage.example.com
dendrite member add recon alice@example.com runner --server https://lineage.example.com
```

With `WORKSPACE_DOMAIN` set, anyone from that domain who signs in joins every existing project as a viewer, and admins promote people from there. Then, in a repo:

```bash
dendrite init --server https://lineage.example.com --project recon    # commit dendrite.toml
dendrite push --all                                                    # bring runs you made locally
python run.py
```

## Operating it

- **Re-run** `deploy.sh` to deploy a new version: it builds from the current commit (`TAG`).
- **Re-check** with `gcloud run jobs execute dendrite-check --region "$REGION" --wait`.
- **Logs:** `gcloud run services logs read dendrite --region "$REGION"`.
- **Scaling.** Each instance holds up to `max_open_projects × 5 + 15` Postgres connections (55 with the template's 8). Raise Cloud SQL's `max_connections` (or its tier) before raising `--max-instances`.
- **Costs** are mostly the always-on instance (`--min-instances 1`, CPU always allocated) and the Cloud SQL tier. Set `--min-instances 0` to save money, at the price of cold starts on the first request.
