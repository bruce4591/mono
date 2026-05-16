# Realtime Pin Paper Strategy

## Scope

`crypto_pin_rebound_v1` is a paper strategy. It never sends real orders to an
exchange and does not use exchange account credentials.

The first realtime scope is:

- Market: `CRYPTO_FUTURES`
- Symbols: `BTCUSDT`, `ETHUSDT`
- Signal source: the Pin stage detector migrated from `trade/pin_stage_replay.py`
- Inputs: Binance USD-M futures `aggTrade` and `depth20@100ms` streams
- Execution mode: internal paper fills from actual stream prices

The existing M11 alert remains a separate daily technical alert. It is not the
entry source for this Pin strategy.

## Runtime Flow

```text
Binance futures aggTrade + depth20
  -> RealtimePinPaperStrategyEngine
  -> PinStageDetector
  -> paper_position / paper_trade
  -> mobile_alert_event
  -> SSE while app is online, Getui/Expo push while app is offline
  -> App Strategies screen and alert list
```

When kline curve candidates are enabled, the same collector also subscribes to
Binance futures `@kline_1m` streams. Closed 1m candles update the auxiliary
kline candidate cache used by the Pin scorer. The Pin trigger itself remains a
realtime trade/order-book stage event; 1m candles only add score/context.

## Start Command

```bash
market run-pin-strategy-futures-ws
```

For a dry run:

```bash
market run-pin-strategy-futures-ws --dry-run
```

For explicit symbols:

```bash
market run-pin-strategy-futures-ws --symbol BTCUSDT --symbol ETHUSDT
```

To enable the migrated candidate signal scorer, pass the same learned threshold
JSON files produced by the research scripts:

```bash
market run-pin-strategy-futures-ws \
  --down-wick-signal-thresholds-json data/pin/down_wick_thresholds.json \
  --up-wick-signal-thresholds-json data/pin/up_wick_thresholds.json \
  --trend-break-filter-thresholds-json data/pin/trend_break_thresholds.json \
  --min-candidate-signal-score 1.0 \
  --min-trend-filter-score 1.0
```

To enable the migrated kline curve candidate path:

```bash
market run-pin-strategy-futures-ws \
  --enable-kline-curve-candidates \
  --kline-candidate-windows 1,15 \
  --kline-candidate-directions down_flush \
  --kline-candidate-min-curve-score 0.80 \
  --kline-candidate-lookahead-seconds 180 \
  --kline-candidate-cluster-seconds 900
```

If no threshold JSON files and no kline-curve flag are supplied, runtime behavior
stays on the base realtime stage detector plus paper executor.

By default, the collector archives every raw websocket payload for replay and
backtests:

```text
data/ws_archive/binance_futures_trade_book/YYYY-MM-DD.jsonl.gz
```

The archive is one gzip-compressed JSONL file per UTC day. Each line is the raw
combined-stream payload from Binance, before the strategy parser mutates or
drops anything. Override the path with:

```bash
market run-pin-strategy-futures-ws --archive-dir /path/to/archive
```

## Paper Fill Rules

- `rebound_confirmed` + `down_flush`: open simulated long positions.
- The live paper executor keeps the original adaptive sizing shape from
  `trade/pin_label_backtest.py`: it reads `entry_order_slices[]` when present,
  opens every immediate `rebound_confirmed` slice, and preserves deferred slice
  fields in the signal payload for the next execution step.
- Multiple open paper positions per strategy/symbol are allowed. The old
  single-open-position guard has been removed.
- Position quantity, notional, entry fee, exit fee, realized PnL, and realized
  return are stored with paper positions/trades.
- Every new trade tick first checks existing positions against the same
  take-profit model used by the offline backtest: `take_profit_pct = 0.006`
  and `fee_rate = 0.0006`.
- `rebound_confirmed` + `up_squeeze` closes remaining open long positions as
  the opposite rebound signal. `trend_break` is kept as a non-entry warning path
  rather than a reverse trade.

## Research Modules Kept

The live package keeps the original research modules under `src/market/trade/`
so later scoring work can use the same logic instead of a simplified rewrite:

- `pin_label_backtest.py`: fixed/adaptive paper execution, candidate scoring,
  and kline curve candidate selection.
- `pin_kline_labels.py`: Binance 1m kline label generation.
- `pin_threshold_learning.py`: learned threshold and candidate signal scoring.
- `pin_extreme_calibration.py`: extreme move calibration helpers.

The realtime engine already persists the full `StageEvent.to_label()` payload,
including `entry_order_slices`, `entry_order_notional`, `entry_order_allocation_ratio`,
trade/order-book feature windows, and kline/scorer fields when they are present.

## Mobile

The App Strategies screen reads:

```text
GET /api/mobile/strategies
```

The alert list uses the existing mobile alert event pipeline. Strategy events
use `source_type = strategy` and `condition_type = paper_strategy_pin`.

When a paper trade opens or closes, the websocket collector immediately invokes
the existing mobile push delivery worker for that event timestamp. The event
metadata uses `signal_timeframe = realtime` and `chart_period = 1m`. This keeps
the Pin signal modeled as a realtime trade/order-book event while still letting
the App open the 1m K-line and draw the same event as a chart marker. The 1m
K-line is auxiliary context for scoring and review, not the signal source.
