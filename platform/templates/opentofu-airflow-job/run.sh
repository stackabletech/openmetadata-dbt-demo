#!/bin/sh
set -e

WORKDIR="/tmp/tofu-work"
mkdir -p "$WORKDIR"
cp /tf/*.tf "$WORKDIR/"
cd "$WORKDIR"

# Pass Airflow credentials as TF variables
export TF_VAR_airflow_base_url="$AIRFLOW_BASE_URL"
export TF_VAR_airflow_username="$AIRFLOW_USERNAME"
export TF_VAR_airflow_password="$AIRFLOW_PASSWORD"

echo "=== Initializing OpenTofu ==="
tofu init

echo "=== Applying configuration ==="
tofu apply -auto-approve

echo "=== Done ==="
