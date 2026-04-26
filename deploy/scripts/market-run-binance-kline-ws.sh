#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
DB_PATH=${MARKET_DB_PATH:-$APP_DIR/data/market.sqlite3}
SYMBOLS=${MARKET_WS_SYMBOLS:-BTCUSDT ETHUSDT SOLUSDT}

cd "$APP_DIR"

args=()
for symbol in $SYMBOLS; do
  args+=(--symbol "$symbol")
done

.venv/bin/market run-binance-kline-ws \
  --db-path "$DB_PATH" \
  "${args[@]}" \
  --interval 1m
