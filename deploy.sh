#!/usr/bin/env bash
# Deploy the RiverScout MCP server to Cloud Run.
# Usage: PROJECT_ID=my-gcp-project ./deploy.sh
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID, e.g. PROJECT_ID=my-gcp-project ./deploy.sh}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-river-scout-mcp}"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  --project "$PROJECT_ID"

# --source builds the Dockerfile with Cloud Build and stores the image in
# Artifact Registry (creating the repo on first use); no separate build step.
gcloud run deploy "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --source . \
  --port 8080 \
  --allow-unauthenticated \
  --max-instances 3 \
  --memory 512Mi

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format 'value(status.url)')"
echo
echo "Deployed. Health check:  curl $URL/health"
echo "MCP endpoint (use this in Gemini Enterprise):  $URL/mcp"
