#!/usr/bin/env bash
set -euo pipefail

required=(AIOS_INGRESS_TOKEN AGENTZERO_DPR_ID AGENTZERO_MANAGER_DPR_ID AIOS_NATS_URL)
for key in "${required[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    echo "missing required env: $key"
    exit 1
  fi
done

while IFS= read -r line; do
  [[ -z "$line" || "$line" != *_SECRET_NAME=* ]] && continue
  key="${line%%=*}"
  name="${line#*=}"
  base="${key%_SECRET_NAME}"
  if [[ -z "$name" ]]; then
    echo "empty secret name for $key"
    exit 1
  fi
  if [[ -z "${!base:-}" ]]; then
    echo "resolved secret missing for $base (from $key=$name)"
    exit 1
  fi
done < <(env)

python -m pip install -r requirements.txt
python -m python.helpers.aios_nats_worker
