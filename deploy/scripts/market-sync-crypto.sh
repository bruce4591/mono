#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
DB_PATH=${MARKET_DB_PATH:-$APP_DIR/data/market.sqlite3}

cd "$APP_DIR"

.venv/bin/market sync-crypto-board \
  --db-path "$DB_PATH" \
  --symbol BTCUSDT \
  --symbol ETHUSDT \
  --interval 15m \
  --limit 96 \
  --board-name CRYPTO_TURNOVER_TOP50 \
  --board-limit 50
