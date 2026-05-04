#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
DB_PATH=${MARKET_DB_PATH:-$APP_DIR/data/market.sqlite3}

cd "$APP_DIR"

.venv/bin/market evaluate-mobile-alerts --db-path "$DB_PATH"
