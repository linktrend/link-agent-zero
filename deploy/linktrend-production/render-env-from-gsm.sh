#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$ROOT_DIR/deploy/linktrend-production/.env"
OUT_FILE="$ROOT_DIR/deploy/linktrend-production/.env.runtime"

[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE"; exit 1; }
command -v gcloud >/dev/null 2>&1 || { echo "gcloud CLI required"; exit 1; }

PROJECT_ID="${GCP_PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-$(awk -F= '/^(GCP_PROJECT_ID|GOOGLE_CLOUD_PROJECT)=/{print $2; exit}' "$ENV_FILE")}}"
[[ -n "$PROJECT_ID" ]] || { echo "Missing GCP project id"; exit 1; }

cp "$ENV_FILE" "$OUT_FILE"
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# || "$line" != *=* ]] && continue
  key="${line%%=*}"; value="${line#*=}"
  [[ "$key" == *_SECRET_NAME ]] || continue
  base="${key%_SECRET_NAME}"
  resolved="$(gcloud secrets versions access latest --project "$PROJECT_ID" --secret "$value")"
  sed -i.bak "/^${base}=/d" "$OUT_FILE" || true
  rm -f "$OUT_FILE.bak"
  printf "\n%s=%s\n" "$base" "$resolved" >> "$OUT_FILE"
done < "$ENV_FILE"
chmod 600 "$OUT_FILE"
echo "Generated $OUT_FILE"
