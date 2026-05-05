#!/usr/bin/env bash
set -euo pipefail

cd /home/ubuntu/github/mono

if [[ -f .market.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .market.env
  set +a
fi

if [[ -z "${MARKET_DATABASE_URL:-}" ]]; then
  echo "MARKET_DATABASE_URL is required" >&2
  exit 1
fi

SQLITE_DB="${MARKET_DB_PATH:-/home/ubuntu/github/mono/data/market.sqlite3}"
REPORT_PATH="/home/ubuntu/github/mono/logs/sqlite-count-report.$(date +%Y%m%d%H%M%S).csv"

mkdir -p /home/ubuntu/github/mono/logs

PYTHONPATH=src .venv/bin/market init-postgres-db --database-url "$MARKET_DATABASE_URL"
PYTHONPATH=src .venv/bin/market sqlite-count-report --sqlite-db "$SQLITE_DB" \
  | tee "$REPORT_PATH"
PYTHONPATH=src .venv/bin/market backfill-postgres \
  --sqlite-db "$SQLITE_DB" \
  --postgres-url "$MARKET_DATABASE_URL"
