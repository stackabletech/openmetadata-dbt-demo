#!/bin/bash
set -e

TRINO_HOST="${TRINO_HOST:-trino-coordinator}"
TRINO_PORT="${TRINO_PORT:-8443}"
SQL_DIR="${SQL_DIR:-/sql}"

TRINO_ARGS="--insecure --server https://${TRINO_HOST}:${TRINO_PORT}"

if [ -f /credentials/username ]; then
  TRINO_ARGS="$TRINO_ARGS --user $(cat /credentials/username) --password"
  export TRINO_PASSWORD=$(cat /credentials/password)
fi

for f in $(ls "$SQL_DIR"/*.sql | sort); do
  echo "=== Executing $f ==="
  trino $TRINO_ARGS -f "$f"
  echo "=== Done: $f ==="
done

echo "All SQL files executed successfully."
