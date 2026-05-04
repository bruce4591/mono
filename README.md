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

Sync Binance Spot 365-day daily bars:

```bash
.venv/bin/market sync-binance-daily \
  --db-path ./data/market.sqlite3 \
  --symbol BTCUSDT \
  --days 365
```

Sync daily bars for Binance USDT quoteVolume Top50:

```bash
.venv/bin/market sync-crypto-daily \
  --db-path ./data/market.sqlite3 \
  --days 365 \
  --top-usdt-limit 50
```

Sync the crypto board in one command. If no `--symbol` is provided, the command
selects a Binance USDT quoteVolume candidate pool before refreshing 24h
snapshots and the board. Use `--skip-kline-sync` when WebSocket 1m collection is
running; it avoids REST K line pulls and does not aggregate local 1m bars, so
the scheduled board job stays lightweight:

```bash
.venv/bin/market sync-crypto-board \
  --db-path ./data/market.sqlite3 \
  --interval 1m \
  --limit 96 \
  --top-usdt-limit 60 \
  --skip-kline-sync
```

Sync Binance USD-M futures boards without K-line pulls:

```bash
.venv/bin/market sync-crypto-futures-boards \
  --db-path ./data/market.sqlite3 \
  --board-limit 50
```

This command refreshes `CRYPTO_FUTURES_TURNOVER_TOP50` and
`CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50`. It does not subscribe to WebSockets or
aggregate local bars.

Aggregate higher Binance USD-M futures K line intervals from local 1m bars
without REST calls:

```bash
.venv/bin/market aggregate-crypto-futures-klines \
  --db-path ./data/market.sqlite3 \
  --top-usdt-limit 60
```

Aggregate higher crypto K line intervals from local 1m bars without REST calls.
The aggregator starts from the last existing target interval bar, then only
uses later 1m bars to upsert 5m/15m/8h/1d windows:

```bash
.venv/bin/market aggregate-crypto-klines \
  --db-path ./data/market.sqlite3 \
  --top-usdt-limit 60
```

Run the optional Binance 1m kline WebSocket collector after installing
`websocket-client` into the Python environment:

```bash
.venv/bin/market run-binance-kline-ws \
  --db-path ./data/market.sqlite3 \
  --top-usdt-limit 60 \
  --interval 1m
```

Run the Binance USD-M futures 1m kline WebSocket collector separately. It uses
the same top-60 default and fills missing windows only after reconnect gaps:

```bash
.venv/bin/market run-binance-futures-kline-ws \
  --db-path ./data/market.sqlite3 \
  --top-usdt-limit 60 \
  --interval 1m \
  --gap-fill-on-reconnect
```

Fill missing 1m crypto K line windows with REST only when gaps exist:

```bash
.venv/bin/market fill-crypto-kline-gaps \
  --db-path ./data/market.sqlite3 \
  --lookback-minutes 180 \
  --top-usdt-limit 60
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
- Historical K lines and missing 1m windows are filled by REST / scheduled jobs.
  Ranking refresh uses scheduled 24h snapshots and does not need sub-second
  latency, so it can run every few minutes.
- Real-time crypto 1m K lines use Binance WebSocket combined streams through
  `run-binance-kline-ws`. The collector writes `bar_intraday(interval='1m',
  source='binance_ws_kline')` only, keeping the realtime path lightweight.
  REST / scheduled jobs fill missing windows and derive 5m/15m/8h/1d bars.
- Real-time Binance USD-M futures 1m K lines use
  `run-binance-futures-kline-ws`. The collector writes
  `bar_intraday(interval='1m', source='binance_futures_ws_kline')` under
  `market='CRYPTO_FUTURES'`; TradeFi uses the same futures K-line storage.
  Futures ranking sync remains separate and does not pull K lines.
- Binance WebSocket connections disconnect at 24 hours and incoming control
  messages are limited to `5` per second. The collector uses grouped combined
  streams and exposes conservative throttling/reconnect constants; the packaged
  systemd service restarts the process automatically.

Note: the current chart page loads `klinecharts@9.8.12` from jsDelivr. If you need fully offline LAN usage later, vendor the standalone JS file into `frontend/` and serve it locally.

### Android self-use app and OnePlus 13T alerts

The Android app uses native notifications. On OnePlus 13T, enable:

- Notification permission.
- Lock-screen notifications.
- Battery setting: no optimization or allow background running.
- Auto-start/background launch permission.
- High power usage whitelist if the system prompts.

Foreground alerts can poll the market API every 15-30 seconds. Background,
locked-screen, or killed-app alerts must be evaluated on `tencent-market` and
delivered by push notification because Android may suspend app timers.

### Mobile alert worker

Run the server-side alert evaluator on `tencent-market` so Android background,
locked-screen, and killed-app reminders do not depend on local app polling.

Recommended MVP schedule:

```cron
* * * * * cd /home/ubuntu/github/mono && deploy/scripts/market-evaluate-mobile-alerts.sh >> logs/mobile-alerts.log 2>&1
```

Crypto/futures prices are already updated by WebSocket snapshots, so the worker
only needs to evaluate latest snapshots and send push messages. Cooldowns are
stored in SQLite to avoid repeated notifications.

Build the self-use APK from the Expo app directory:

```bash
cd apps/market-mobile
npm run build:android
```

The build script increments the mobile app patch version and Android
`versionCode` before calling EAS, so each installed APK has a visible version
number in Android app details. To set a specific version manually, run:

```bash
MARKET_MOBILE_VERSION=0.1.8 MARKET_MOBILE_VERSION_CODE=8 npm run build:android
```

Install the APK on the phone, open the app once, grant notification permission,
and confirm that the device appears in the server `push_device` table.

## Next Implementation Slice

- AKShare TradFi focus boards are in place:
  - `sync-akshare-focus` syncs A-SH, HK, US, ETF, IDX, and CMDTY focus boards
    through the existing collector contract;
  - `deploy/scripts/market-sync-akshare-focus.sh` is used by the US-session
    2-hour refresh cron plus the post-close fallback cron;
  - expand beyond the focus pools only after the small daily sync is stable.
- Harden personal mobile access before leaving the API broadly reachable:
  - prefer IP restriction when the client network is stable;
  - otherwise add a simple shared token or Nginx basic auth;
  - move to domain + HTTPS once domain review/备案 is ready.
- Keep the Binance WebSocket collector as the realtime crypto K-line path. The
  remaining realtime gap is a separate ticker/miniTicker price collector if
  sub-minute latest-price updates become necessary.
