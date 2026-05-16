#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
ENV_FILE=${MARKET_ENV_FILE:-$APP_DIR/.market.env}
SYMBOLS=${MARKET_PIN_STRATEGY_SYMBOLS:-BTCUSDT ETHUSDT}
MAX_STREAMS_PER_CONNECTION=${MARKET_PIN_STRATEGY_MAX_STREAMS_PER_CONNECTION:-100}
ARCHIVE_DIR=${MARKET_PIN_STRATEGY_ARCHIVE_DIR:-$APP_DIR/data/ws_archive/binance_futures_trade_book}

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

args+=(--max-streams-per-connection "$MAX_STREAMS_PER_CONNECTION")
args+=(--archive-dir "$ARCHIVE_DIR")

if [[ -n "${MARKET_PIN_DOWN_WICK_THRESHOLDS_JSON:-}" ]]; then
  args+=(--down-wick-signal-thresholds-json "$MARKET_PIN_DOWN_WICK_THRESHOLDS_JSON")
fi
if [[ -n "${MARKET_PIN_UP_WICK_THRESHOLDS_JSON:-}" ]]; then
  args+=(--up-wick-signal-thresholds-json "$MARKET_PIN_UP_WICK_THRESHOLDS_JSON")
fi
if [[ -n "${MARKET_PIN_TREND_BREAK_THRESHOLDS_JSON:-}" ]]; then
  args+=(--trend-break-filter-thresholds-json "$MARKET_PIN_TREND_BREAK_THRESHOLDS_JSON")
fi
if [[ -n "${MARKET_PIN_MIN_CANDIDATE_SIGNAL_SCORE:-}" ]]; then
  args+=(--min-candidate-signal-score "$MARKET_PIN_MIN_CANDIDATE_SIGNAL_SCORE")
fi
if [[ -n "${MARKET_PIN_MIN_TREND_FILTER_SCORE:-}" ]]; then
  args+=(--min-trend-filter-score "$MARKET_PIN_MIN_TREND_FILTER_SCORE")
fi

case "${MARKET_PIN_ENABLE_KLINE_CURVE_CANDIDATES:-}" in
  1|true|TRUE|yes|YES)
    args+=(--enable-kline-curve-candidates)
    ;;
esac
if [[ -n "${MARKET_PIN_KLINE_CANDIDATE_WINDOWS:-}" ]]; then
  args+=(--kline-candidate-windows "$MARKET_PIN_KLINE_CANDIDATE_WINDOWS")
fi
if [[ -n "${MARKET_PIN_KLINE_CANDIDATE_DIRECTIONS:-}" ]]; then
  args+=(--kline-candidate-directions "$MARKET_PIN_KLINE_CANDIDATE_DIRECTIONS")
fi
if [[ -n "${MARKET_PIN_KLINE_CANDIDATE_MIN_CURVE_SCORE:-}" ]]; then
  args+=(--kline-candidate-min-curve-score "$MARKET_PIN_KLINE_CANDIDATE_MIN_CURVE_SCORE")
fi
if [[ -n "${MARKET_PIN_KLINE_CANDIDATE_LOOKAHEAD_SECONDS:-}" ]]; then
  args+=(--kline-candidate-lookahead-seconds "$MARKET_PIN_KLINE_CANDIDATE_LOOKAHEAD_SECONDS")
fi
if [[ -n "${MARKET_PIN_KLINE_CANDIDATE_HISTORY_SIZE:-}" ]]; then
  args+=(--kline-candidate-history-size "$MARKET_PIN_KLINE_CANDIDATE_HISTORY_SIZE")
fi
if [[ -n "${MARKET_PIN_KLINE_CANDIDATE_CLUSTER_SECONDS:-}" ]]; then
  args+=(--kline-candidate-cluster-seconds "$MARKET_PIN_KLINE_CANDIDATE_CLUSTER_SECONDS")
fi
if [[ -n "${MARKET_PIN_KLINE_CANDIDATE_MIN_CLUSTER_SCORE:-}" ]]; then
  args+=(--kline-candidate-min-cluster-score "$MARKET_PIN_KLINE_CANDIDATE_MIN_CLUSTER_SCORE")
fi
case "${MARKET_PIN_STRATEGY_DRY_RUN:-}" in
  1|true|TRUE|yes|YES)
    args+=(--dry-run)
    ;;
esac

mkdir -p "$ARCHIVE_DIR"

.venv/bin/market run-pin-strategy-futures-ws \
  ${db_args[@]+"${db_args[@]}"} \
  "${args[@]}"
