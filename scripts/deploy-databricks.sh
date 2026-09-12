#!/usr/bin/env bash
# Deploy Microguard dashboard to Databricks Apps
# Usage: ./scripts/deploy-databricks.sh [--env production|staging]

set -euo pipefail

ENV="${1:-production}"
APP_NAME="microguard-dashboard"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "🚀 Deploying Microguard Dashboard to Databricks Apps (${ENV})"

# Check prerequisites
if ! command -v databricks &> /dev/null; then
    echo "❌ Error: databricks CLI not found"
    echo "   Install with: pip install databricks-cli"
    exit 1
fi

if [ -z "${DATABRICKS_HOST:-}" ]; then
    echo "❌ Error: DATABRICKS_HOST not set"
    echo "   export DATABRICKS_HOST='https://dbc-xxxxxxx.xxxx.databricks.com'"
    exit 1
fi

if [ -z "${DATABRICKS_TOKEN:-}" ]; then
    echo "❌ Error: DATABRICKS_TOKEN not set"
    echo "   export DATABRICKS_TOKEN='your-token-here'"
    exit 1
fi

# Check if app exists
echo "🔍 Checking if app exists..."
if databricks apps get "${APP_NAME}" &> /dev/null; then
    echo "📦 Updating existing app: ${APP_NAME}"
    databricks apps update "${APP_NAME}" \
        --source "${PROJECT_DIR}/databricks-app" \
        --env "${ENV}"
else
    echo "📦 Creating new app: ${APP_NAME}"
    databricks apps create "${APP_NAME}" \
        --source "${PROJECT_DIR}/databricks-app" \
        --env "${ENV}" \
        --description "Microguard Bot Traffic Detection Dashboard"
fi

# Get the app URL
echo "🔗 Getting app URL..."
APP_URL=$(databricks apps get "${APP_NAME}" --output json 2>/dev/null | \
    python3 -c "import sys, json; print(json.load(sys.stdin).get('url', ''))" 2>/dev/null || echo "")

if [ -n "${APP_URL}" ]; then
    echo ""
    echo "✅ Deployment complete!"
    echo "   App URL: ${APP_URL}"
    echo "   Health: ${APP_URL}/api/health"
    echo "   Model: ${APP_URL}/api/model/info"
    echo "   MLflow: ${APP_URL}/api/mlflow/runs"
else
    echo ""
    echo "✅ Deployment complete! Check Databricks Apps console for URL."
fi
