#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
DB_PATH=${MARKET_DB_PATH:-$APP_DIR/data/market.sqlite3}
TOP_USDT_LIMIT=${MARKET_AGGREGATE_TOP_USDT_LIMIT:-60}

cd "$APP_DIR"

.venv/bin/market aggregate-crypto-klines \
  --db-path "$DB_PATH" \
  --top-usdt-limit "$TOP_USDT_LIMIT"
