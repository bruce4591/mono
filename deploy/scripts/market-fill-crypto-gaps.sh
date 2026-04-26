#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
DB_PATH=${MARKET_DB_PATH:-$APP_DIR/data/market.sqlite3}
LOOKBACK_MINUTES=${MARKET_GAP_LOOKBACK_MINUTES:-180}
TOP_USDT_LIMIT=${MARKET_GAP_TOP_USDT_LIMIT:-60}

cd "$APP_DIR"

.venv/bin/market fill-crypto-kline-gaps \
  --db-path "$DB_PATH" \
  --lookback-minutes "$LOOKBACK_MINUTES" \
  --top-usdt-limit "$TOP_USDT_LIMIT"
