#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
DB_PATH=${MARKET_DB_PATH:-$APP_DIR/data/market.sqlite3}
ENV_FILE=${MARKET_ENV_FILE:-$APP_DIR/.market.env}

cd "$APP_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

AKSHARE_CONFIGS=(
  config/watchlists/a_share_focus20.json
  config/watchlists/hk_stock_focus20.json
  config/watchlists/index_focus20.json
  config/watchlists/commodity_focus20.json
)

ALPACA_CONFIGS=(
  config/watchlists/us_stock_focus20.json
  config/watchlists/etf_focus20.json
)

for config in "${AKSHARE_CONFIGS[@]}"; do
  .venv/bin/market sync-akshare-focus \
    --db-path "$DB_PATH" \
    --days 365 \
    --board-limit 30 \
    --request-timeout-seconds 30 \
    --watchlist-config "$config"
done

alpaca_args=()
for config in "${ALPACA_CONFIGS[@]}"; do
  alpaca_args+=(--watchlist-config "$config")
done

.venv/bin/market sync-alpaca-focus \
  --db-path "$DB_PATH" \
  --days 365 \
  --board-limit 30 \
  --request-timeout-seconds 30 \
  "${alpaca_args[@]}"
