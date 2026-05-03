#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
DB_PATH=${MARKET_DB_PATH:-$APP_DIR/data/market.sqlite3}
SYMBOLS=${MARKET_WS_SYMBOLS:-}
TOP_USDT_LIMIT=${MARKET_WS_TOP_USDT_LIMIT:-60}

cd "$APP_DIR"

args=()
for symbol in $SYMBOLS; do
  args+=(--symbol "$symbol")
done
if [[ ${#args[@]} -eq 0 ]]; then
  args+=(--top-usdt-limit "$TOP_USDT_LIMIT")
fi

.venv/bin/market run-binance-kline-ws \
  --db-path "$DB_PATH" \
  "${args[@]}" \
  --interval 1m \
  --gap-fill-on-reconnect
