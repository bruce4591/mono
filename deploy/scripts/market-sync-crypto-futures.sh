#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
DB_PATH=${MARKET_DB_PATH:-$APP_DIR/data/market.sqlite3}
BOARD_LIMIT=${MARKET_FUTURES_BOARD_LIMIT:-50}

cd "$APP_DIR"

.venv/bin/market sync-crypto-futures-boards \
  --db-path "$DB_PATH" \
  --board-limit "$BOARD_LIMIT"
