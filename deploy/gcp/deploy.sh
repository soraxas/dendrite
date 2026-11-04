#!/usr/bin/env bash
# Deploy the dendrite server to Google Cloud: Cloud Run + Cloud SQL (Postgres) + Cloud Storage,
# with Google Workspace sign-in. Safe to re-run: existing resources are kept, the image and the
# config are updated. See README.md here for the two manual steps (OAuth client and DNS).
#
#   PROJECT=my-project REGION=australia-southeast1 DOMAIN=lineage.example.com \
#   ADMINS=you@example.com WORKSPACE_DOMAIN=example.com \
#   OAUTH_CLIENT_ID=… OAUTH_CLIENT_SECRET=… ./deploy/gcp/deploy.sh
set -euo pipefail

: "${PROJECT:?set PROJECT (the Google Cloud project id)}"
: "${REGION:?set REGION, e.g. australia-southeast1}"
: "${DOMAIN:?set DOMAIN, e.g. lineage.example.com}"
: "${ADMINS:?set ADMINS, comma-separated emails of the first admins}"
: "${OAUTH_CLIENT_ID:?set OAUTH_CLIENT_ID (README: step 1)}"
: "${OAUTH_CLIENT_SECRET:?set OAUTH_CLIENT_SECRET (README: step 1)}"
WORKSPACE_DOMAIN="${WORKSPACE_DOMAIN:-}"        # sign-ins from it join every project as viewers
SERVICE="${SERVICE:-dendrite}"
BUCKET="${BUCKET:-${PROJECT}-dendrite}"
SQL_INSTANCE="${SQL_INSTANCE:-dendrite}"
SQL_TIER="${SQL_TIER:-db-g1-small}"
DB_NAME="${DB_NAME:-dendrite}"
DB_USER="${DB_USER:-dendrite}"
TAG="${TAG:-$(git rev-parse --short HEAD)}"
REPO="${REGION}-docker.pkg.dev/${PROJECT}/dendrite"
IMAGE="${REPO}/server:${TAG}"
SA="dendrite-server@${PROJECT}.iam.gserviceaccount.com"
SQL_CONNECTION="${PROJECT}:${REGION}:${SQL_INSTANCE}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

say() { printf '\n== %s\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }

gcloud config set project "$PROJECT" >/dev/null

say "APIs"
gcloud services enable run.googleapis.com sqladmin.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com storage.googleapis.com cloudbuild.googleapis.com

say "Service account $SA"
exists gcloud iam service-accounts describe "$SA" ||
  gcloud iam service-accounts create dendrite-server --display-name "dendrite server"
# Signing GCS URLs without a key file goes through IAM signBlob on itself.
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --member "serviceAccount:$SA" --role roles/iam.serviceAccountTokenCreator >/dev/null
for role in roles/cloudsql.client roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role "$role" \
    --condition=None >/dev/null
done

say "Bucket gs://$BUCKET (private)"
exists gcloud storage buckets describe "gs://$BUCKET" ||
  gcloud storage buckets create "gs://$BUCKET" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$SA" --role roles/storage.objectAdmin >/dev/null

say "Cloud SQL $SQL_INSTANCE (Postgres 17, $SQL_TIER)"
exists gcloud sql instances describe "$SQL_INSTANCE" ||
  gcloud sql instances create "$SQL_INSTANCE" --database-version POSTGRES_17 --region "$REGION" \
    --tier "$SQL_TIER" --database-flags max_connections=200 --storage-auto-increase
exists gcloud sql databases describe "$DB_NAME" --instance "$SQL_INSTANCE" ||
  gcloud sql databases create "$DB_NAME" --instance "$SQL_INSTANCE"

say "Secrets"
secret() {  # secret NAME VALUE: create, or add a version only when the value changed
  local name=$1 value=$2
  if ! exists gcloud secrets describe "$name"; then
    printf '%s' "$value" | gcloud secrets create "$name" --data-file=- >/dev/null
  elif [ "$(gcloud secrets versions access latest --secret "$name" 2>/dev/null)" != "$value" ]; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- >/dev/null
  fi
}
if ! exists gcloud secrets describe dendrite-db-password; then
  secret dendrite-db-password "$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)"
fi
DB_PASSWORD="$(gcloud secrets versions access latest --secret dendrite-db-password)"
if exists gcloud sql users describe "$DB_USER" --instance "$SQL_INSTANCE"; then
  gcloud sql users set-password "$DB_USER" --instance "$SQL_INSTANCE" --password "$DB_PASSWORD" >/dev/null
else
  gcloud sql users create "$DB_USER" --instance "$SQL_INSTANCE" --password "$DB_PASSWORD" >/dev/null
fi
secret dendrite-oauth-client-secret "$OAUTH_CLIENT_SECRET"

quote_list() { [ -z "$1" ] && return; printf '"%s"' "$1" | sed 's/,/", "/g'; }
CONFIG="$(sed \
  -e "s|@DOMAIN@|$DOMAIN|" -e "s|@DB_USER@|$DB_USER|" -e "s|@DB_NAME@|$DB_NAME|" \
  -e "s|@SQL_CONNECTION@|$SQL_CONNECTION|" -e "s|@BUCKET@|$BUCKET|" \
  -e "s|@ADMINS@|$(quote_list "$ADMINS")|" -e "s|@ALLOWED_DOMAINS@|$(quote_list "$WORKSPACE_DOMAIN")|" \
  -e "s|@OAUTH_CLIENT_ID@|$OAUTH_CLIENT_ID|" "$ROOT/deploy/gcp/server.toml.tmpl")"
secret dendrite-server-toml "$CONFIG"

say "Image $IMAGE (built by Cloud Build, for linux/amd64)"
exists gcloud artifacts repositories describe dendrite --location "$REGION" ||
  gcloud artifacts repositories create dendrite --repository-format docker --location "$REGION"
gcloud builds submit "$ROOT" --tag "$IMAGE" --timeout 1800

say "Cloud Run service $SERVICE"
# --no-cpu-throttling: verification of large staged uploads runs after the request returns.
gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" --service-account "$SA" \
  --port 8455 --cpu 1 --memory 1Gi --min-instances 1 --max-instances 2 --timeout 3600 \
  --no-cpu-throttling --allow-unauthenticated \
  --add-cloudsql-instances "$SQL_CONNECTION" \
  --set-secrets "/etc/dendrite/server.toml=dendrite-server-toml:latest,DB_PASSWORD=dendrite-db-password:latest,OAUTH_CLIENT_SECRET=dendrite-oauth-client-secret:latest"

say "Check"
exists gcloud run jobs describe dendrite-check --region "$REGION" && verb=update || verb=create
gcloud run jobs "$verb" dendrite-check --image "$IMAGE" --region "$REGION" --service-account "$SA" \
  --set-cloudsql-instances "$SQL_CONNECTION" \
  --set-secrets "/etc/dendrite/server.toml=dendrite-server-toml:latest,DB_PASSWORD=dendrite-db-password:latest,OAUTH_CLIENT_SECRET=dendrite-oauth-client-secret:latest" \
  --command dendrite --args "server,check,--config,/etc/dendrite/server.toml" >/dev/null
gcloud run jobs execute dendrite-check --region "$REGION" --wait

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"
say "Done"
echo "Cloud Run URL: $URL"
echo "Map https://$DOMAIN to it (README: step 3), then sign in at https://$DOMAIN as one of: $ADMINS"
