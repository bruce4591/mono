# Market MVP

Local-first multi-market data, ranking, and charting system.

## Current Slice

Implemented foundation:

- Python package under `src/market`
- SQLite schema initialization
- WAL mode for local file database reads/writes
- Instrument repository with idempotent `market + symbol` upsert
- Daily bar repository with idempotent `instrument_id + trade_date` upsert
- Intraday bar repository with idempotent `instrument_id + interval + bar_start_ts_utc` upsert
- Watchlist repository that can replace active Focus lists while preserving inactive history
- Static watchlist JSON import for ETF Focus20 and Index Focus20
- Market snapshot repository and turnover ranking refresh logic
- Fake collector command that writes sample ETF and crypto snapshots, daily bars, and intraday bars
- Read-only local JSON API for board data
- Mobile web board dashboard served from the local API process
- Instrument detail API and mobile detail page with daily and intraday bars
- Watchlist, health, and job status API endpoints
- Alert rule/event framework with metric resolvers for future indicators
- Unified collector contract with Binance behind a collector adapter
- Mobile status page for boards, alerts, watchlists, jobs, and data sources
- KLineCharts candlestick panel with MA, VOL, MACD, interval, volume, and turnover columns
- CLI commands for database initialization and health checks

## Local Commands

Create and use a local Python environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Run tests:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Initialize a local database:

```bash
.venv/bin/market init-db --db-path ./data/market.sqlite3
```

Check database health:

```bash
.venv/bin/market health-check --db-path ./data/market.sqlite3
```

Sync static watchlists:

```bash
.venv/bin/market sync-watchlists --db-path ./data/market.sqlite3 --path ./config/watchlists/etf_focus20.json
.venv/bin/market sync-watchlists --db-path ./data/market.sqlite3 --path ./config/watchlists/index_focus20.json
```

Seed fake local market data:

```bash
.venv/bin/market seed-sample-data \
  --db-path ./data/market.sqlite3 \
  --snapshot-ts-utc 2026-04-24T20:00:00Z \
  --trade-date-local 2026-04-24
```

Sync real Binance Spot 15m crypto bars:

```bash
.venv/bin/market sync-binance-klines \
  --db-path ./data/market.sqlite3 \
  --symbol BTCUSDT \
  --interval 15m \
  --limit 96
```

Sync the crypto board in one command:

```bash
.venv/bin/market sync-crypto-board \
  --db-path ./data/market.sqlite3 \
  --symbol BTCUSDT \
  --symbol ETHUSDT \
  --interval 15m \
  --limit 96
```

Apply one Binance ticker WebSocket event payload to the latest snapshot path:

```bash
.venv/bin/market apply-binance-ticker-event \
  --db-path ./data/market.sqlite3 \
  --path ./ticker-event.json
```

Add and evaluate a simple alert rule:

```bash
.venv/bin/market add-alert-rule \
  --db-path ./data/market.sqlite3 \
  --name "btc change high" \
  --market CRYPTO \
  --symbol BTCUSDT \
  --metric change_pct \
  --operator ">=" \
  --threshold 2

.venv/bin/market run-alerts --db-path ./data/market.sqlite3
.venv/bin/market list-alert-events --db-path ./data/market.sqlite3 --limit 20
```

Refresh a turnover board from current snapshots:

```bash
.venv/bin/market refresh-rankings \
  --db-path ./data/market.sqlite3 \
  --board-name ETF_FOCUS20 \
  --snapshot-ts-utc 2026-04-24T20:00:00Z \
  --trade-date-local 2026-04-24 \
  --market US \
  --instrument-type etf \
  --limit 20 \
  --watchlist-name ETF_FOCUS20
```

Serve the local read-only API:

```bash
.venv/bin/market serve-api --db-path ./data/market.sqlite3 --host 127.0.0.1 --port 8000
```

Open a board JSON endpoint:

```text
http://127.0.0.1:8000/
http://127.0.0.1:8000/status.html
http://127.0.0.1:8000/instrument.html?market=US&symbol=SPY
http://127.0.0.1:8000/api/health
http://127.0.0.1:8000/api/watchlists
http://127.0.0.1:8000/api/jobs
http://127.0.0.1:8000/api/alerts/metrics
http://127.0.0.1:8000/api/alerts/rules
http://127.0.0.1:8000/api/alerts/events
http://127.0.0.1:8000/api/boards/ETF_FOCUS20
http://127.0.0.1:8000/api/boards/CRYPTO_TURNOVER_TOP50
http://127.0.0.1:8000/api/instruments/US/SPY
http://127.0.0.1:8000/api/bars/daily?market=US&symbol=SPY
http://127.0.0.1:8000/api/bars/intraday?market=CRYPTO&symbol=BTCUSDT&interval=15m
```

## MVP Direction

- Storage: SQLite file database
- Jobs: local CLI commands, later scheduled by cron
- API: read-only service in a separate process
- Client: mobile Web/PWA using KLineCharts
- Later extension: indicators and alerts on top of stored bars and rankings

## Collector Safety Policy

- Collectors should rate-limit requests by default. Binance Spot `GET /api/v3/klines`
  has request weight `2`; this project caps Binance REST usage at a conservative
  `120` request-weight/minute budget, so the current Binance collector waits at
  least `1` second between symbol kline requests.
- Collectors should not retry failed requests by default. Binance returns HTTP
  `429` when a request rate limit is broken and HTTP `418` after repeated
  violations; a failure records `job_state` and `source_health`, then stops the
  job so it does not hammer the source.
- Future AKShare collectors should use the same collector wrapper and start with small focus pools before expanding coverage.
- Historical K lines, turnover, and ranking refresh jobs should stay on REST /
  scheduled collectors. Ranking refresh does not need sub-second latency and can
  run every few minutes.
- Real-time crypto prices should use Binance WebSocket streams in a later slice.
  Binance WS allows up to `1024` streams on one connection, limits incoming
  control messages to `5` per second, and disconnects connections at 24 hours, so
  the implementation should use grouped combined streams, throttle
  subscribe/unsubscribe messages, and reconnect cleanly.
- Current realtime groundwork can already parse Binance `24hrTicker` /
  `24hrMiniTicker` payloads and update `market_snapshot` through
  `apply-binance-ticker-event`; the actual long-running WS connection runner is
  intentionally deferred until a WebSocket client dependency is approved.

Note: the current chart page loads `klinecharts@9.8.12` from jsDelivr. If you need fully offline LAN usage later, vendor the standalone JS file into `frontend/` and serve it locally.

## Next Implementation Slice

- Then wire Binance crypto 15m as the first real data source.
