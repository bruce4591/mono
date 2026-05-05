#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
ENV_FILE=${MARKET_ENV_FILE:-$APP_DIR/.market.env}
SYMBOLS=${MARKET_FUTURES_WS_SYMBOLS:-}
TOP_USDT_LIMIT=${MARKET_FUTURES_WS_TOP_USDT_LIMIT:-60}

cd "$APP_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

args=()
db_args=()
if [[ -z "${MARKET_DATABASE_URL:-}" ]]; then
  DB_PATH=${MARKET_DB_PATH:-$APP_DIR/data/market.sqlite3}
  db_args+=(--db-path "$DB_PATH")
fi
for symbol in $SYMBOLS; do
  args+=(--symbol "$symbol")
done
if [[ ${#args[@]} -eq 0 ]]; then
  args+=(--top-usdt-limit "$TOP_USDT_LIMIT")
fi

.venv/bin/market run-binance-futures-kline-ws \
  "${db_args[@]}" \
  "${args[@]}" \
  --interval 1m \
  --gap-fill-on-reconnect
