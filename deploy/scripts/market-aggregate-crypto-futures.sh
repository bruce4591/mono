#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
ENV_FILE=${MARKET_ENV_FILE:-$APP_DIR/.market.env}
TOP_USDT_LIMIT=${MARKET_FUTURES_AGGREGATE_TOP_USDT_LIMIT:-60}

cd "$APP_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

db_args=()
if [[ -z "${MARKET_DATABASE_URL:-}" ]]; then
  DB_PATH=${MARKET_DB_PATH:-$APP_DIR/data/market.sqlite3}
  db_args+=(--db-path "$DB_PATH")
fi

.venv/bin/market aggregate-crypto-futures-klines \
  "${db_args[@]}" \
  --top-usdt-limit "$TOP_USDT_LIMIT"
