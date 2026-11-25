#!/usr/bin/env bash
set -euo pipefail

PROJECT="kerjasama-dev"
REGION="europe-west1"
SERVICE="cobra-ma-api"
SA_NAME="cobra-ma-api"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/cobra-ma/cobra-ma-api"

echo "==> Running tests..."
python -m pytest tests/ -v
echo "==> All tests passed!"

echo "==> Creating Artifact Registry repo (if needed)..."
gcloud artifacts repositories create cobra-ma \
  --repository-format=docker \
  --location="${REGION}" \
  --project="${PROJECT}" 2>/dev/null || true

echo "==> Creating service account (if needed)..."
gcloud iam service-accounts create "${SA_NAME}" \
  --display-name="COBRA MA API" \
  --project="${PROJECT}" 2>/dev/null || true

echo "==> Granting Cloud Run Invoker role..."
gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker" \
  --condition=None --quiet

echo "==> Creating anthropic-api-key secret (if needed)..."
if ! gcloud secrets describe anthropic-api-key --project="${PROJECT}" &>/dev/null; then
  echo "Secret anthropic-api-key does not exist."
  read -rp "Enter Anthropic API key (or press Enter to skip): " api_key
  if [ -n "${api_key}" ]; then
    echo -n "${api_key}" | gcloud secrets create anthropic-api-key \
      --data-file=- --project="${PROJECT}"
  else
    echo "Skipping secret creation. Create it manually before deploying."
  fi
fi

echo "==> Granting Secret Manager accessor on anthropic-api-key..."
gcloud secrets add-iam-policy-binding anthropic-api-key \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --project="${PROJECT}" --quiet

echo "==> Granting Secret Manager accessor on cloudflare-turnstile-kerjasama-secret-key..."
gcloud secrets add-iam-policy-binding cloudflare-turnstile-kerjasama-secret-key \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --project="${PROJECT}" --quiet

echo "==> Granting Secret Manager accessor on cobra-ma-database-url..."
gcloud secrets add-iam-policy-binding cobra-ma-database-url \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --project="${PROJECT}" --quiet

echo "==> Building with Cloud Build..."
gcloud builds submit \
  --config=cloudbuild.yaml \
  --project="${PROJECT}" \
  --region="${REGION}"

echo "==> Deploying to Cloud Run..."
gcloud run deploy "${SERVICE}" \
  --image="${IMAGE}" \
  --service-account="${SA_EMAIL}" \
  --clear-cloudsql-instances \
  --remove-env-vars="INSTANCE_CONNECTION_NAME,DB_NAME,DB_USER" \
  --set-secrets="ANTHROPIC_API_KEY=anthropic-api-key:latest,TURNSTILE_SECRET_KEY=cloudflare-turnstile-kerjasama-secret-key:latest,DATABASE_URL=cobra-ma-database-url:latest" \
  --region="${REGION}" \
  --memory=512Mi \
  --min-instances=0 \
  --max-instances=3 \
  --allow-unauthenticated \
  --project="${PROJECT}"

echo ""
echo "==> Deployed! Service URL:"
gcloud run services describe "${SERVICE}" \
  --region="${REGION}" --project="${PROJECT}" \
  --format="value(status.url)"
