#!/bin/sh
set -e

WORKDIR="/tmp/tofu-work"
mkdir -p "$WORKDIR"
cp /tf/*.tf "$WORKDIR/"
cd "$WORKDIR"

# Airflow 3 uses JWT auth — obtain a token via /auth/token
echo "=== Obtaining Airflow JWT token ==="
TOKEN=$(wget -qO- --header="Content-Type: application/json" \
  --post-data="{\"username\":\"$AIRFLOW_USERNAME\",\"password\":\"$AIRFLOW_PASSWORD\"}" \
  "$AIRFLOW_BASE_URL/auth/token" | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')

if [ -z "$TOKEN" ]; then
  echo "ERROR: Failed to obtain Airflow JWT token"
  exit 1
fi
echo "  Token obtained successfully."

export TF_VAR_airflow_base_url="$AIRFLOW_BASE_URL"
export TF_VAR_airflow_token="$TOKEN"

echo "=== Initializing OpenTofu ==="
tofu init

echo "=== Importing existing resources ==="
# Import existing connections so apply updates instead of failing on create.
# grep all airflow_connection resource names from .tf files and try to import each.
grep -h 'resource "airflow_connection"' *.tf | sed 's/.*resource "airflow_connection" "\([^"]*\)".*/\1/' | while read -r name; do
  conn_id=$(grep -A5 "resource \"airflow_connection\" \"${name}\"" *.tf | grep connection_id | sed 's/.*= *"\([^"]*\)".*/\1/')
  echo "  Importing airflow_connection.${name} (${conn_id})..."
  tofu import "airflow_connection.${name}" "${conn_id}" 2>/dev/null || echo "  Not found, will create."
done

echo "=== Applying configuration ==="
tofu apply -auto-approve

echo "=== Done ==="
