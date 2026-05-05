# Tencent Lighthouse Deployment

Current deployment target:

```text
Host: tencent-market
App dir: /home/ubuntu/github/mono
DB: /home/ubuntu/github/mono/data/market.sqlite3
API: http://150.109.22.77:8000/
```

## Initial Setup

```bash
git clone -b codex-market-mvp https://github.com/bruce4591/mono.git /home/ubuntu/github/mono
cd /home/ubuntu/github/mono
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
mkdir -p data logs
.venv/bin/market init-db --db-path ./data/market.sqlite3
```

## Run API With systemd

```bash
sudo cp deploy/systemd/market-api.service /etc/systemd/system/market-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now market-api.service
sudo systemctl status market-api.service
```

## Run Binance Kline WebSocket With systemd

The WebSocket service keeps local crypto 1m K lines warm. It defaults to the
Binance USDT quoteVolume Top60 through `MARKET_WS_TOP_USDT_LIMIT=60` in
`deploy/systemd/market-binance-kline-ws.service`. To pin explicit symbols
instead, set `MARKET_WS_SYMBOLS` in the service environment.

```bash
chmod +x deploy/scripts/market-run-binance-kline-ws.sh
sudo cp deploy/systemd/market-binance-kline-ws.service /etc/systemd/system/market-binance-kline-ws.service
sudo systemctl daemon-reload
sudo systemctl enable --now market-binance-kline-ws.service
sudo systemctl status market-binance-kline-ws.service
```

## Run Binance Futures Kline WebSocket With systemd

The futures WebSocket service keeps Binance USD-M futures 1m K lines warm under
`market='CRYPTO_FUTURES'`. It defaults to the futures USDT quoteVolume Top60
through `MARKET_FUTURES_WS_TOP_USDT_LIMIT=60`. TradeFi uses the same futures
K-line storage; it does not need a separate WebSocket service.

```bash
chmod +x deploy/scripts/market-run-binance-futures-kline-ws.sh
sudo cp deploy/systemd/market-binance-futures-kline-ws.service /etc/systemd/system/market-binance-futures-kline-ws.service
sudo systemctl daemon-reload
sudo systemctl enable --now market-binance-futures-kline-ws.service
sudo systemctl status market-binance-futures-kline-ws.service
```

## Sync Crypto Every 15 Minutes

This job refreshes 24h snapshots and the turnover board only. It runs
`sync-crypto-board --skip-kline-sync`, so it does not pull REST K lines or
aggregate local 1m bars; missing K-line windows should be filled only when a
gap is detected.

```bash
mkdir -p /home/ubuntu/bin
cp deploy/scripts/market-sync-crypto.sh /home/ubuntu/bin/market-sync-crypto.sh
chmod +x /home/ubuntu/bin/market-sync-crypto.sh
(crontab -l 2>/dev/null | grep -v market-sync-crypto.sh; echo "*/15 * * * * /home/ubuntu/bin/market-sync-crypto.sh >> /home/ubuntu/github/mono/logs/crypto-sync.log 2>&1") | crontab -
```

## Sync Crypto Futures Boards Every 15 Minutes

This job refreshes Binance USD-M futures 24h snapshots and ranking boards only.
It does not pull K lines, subscribe to WebSockets, or aggregate local bars.

```bash
mkdir -p /home/ubuntu/bin
cp deploy/scripts/market-sync-crypto-futures.sh /home/ubuntu/bin/market-sync-crypto-futures.sh
chmod +x /home/ubuntu/bin/market-sync-crypto-futures.sh
(crontab -l 2>/dev/null | grep -v market-sync-crypto-futures.sh; echo "*/15 * * * * /home/ubuntu/bin/market-sync-crypto-futures.sh >> /home/ubuntu/github/mono/logs/crypto-futures-sync.log 2>&1") | crontab -
```

## Sync AKShare TradFi Focus Boards During Market Sessions

This job refreshes `A_SHARE_FOCUS20`, `HK_STOCK_FOCUS20`,
`US_STOCK_FOCUS20`, `ETF_FOCUS20`, `INDEX_FOCUS20`, and
`COMMODITY_FOCUS20` daily bars, snapshots, and turnover boards through
AKShare. It imports the local watchlist configs first, then syncs only those
small focus pools. The cron entry refreshes every 30 minutes on weekdays.
Board API requests also trigger a best-effort one-board refresh with a
one-minute per-board throttle, so opening the app can pull fresher TradeFi data
without waiting for the next scheduled run.

```bash
mkdir -p /home/ubuntu/bin
cp deploy/scripts/market-sync-akshare-focus.sh /home/ubuntu/bin/market-sync-akshare-focus.sh
chmod +x /home/ubuntu/bin/market-sync-akshare-focus.sh
(crontab -l 2>/dev/null | grep -v market-sync-akshare-focus.sh; echo "*/30 * * * 1-5 /home/ubuntu/bin/market-sync-akshare-focus.sh >> /home/ubuntu/github/mono/logs/akshare-focus-sync.log 2>&1") | crontab -
```

## Aggregate Crypto K Lines Every 5 Minutes

This job only reads local WebSocket 1m bars and incrementally upserts higher
intervals. It does not call Binance REST. Each target interval starts after its
last existing aggregate bar, so a normal 5m run writes only the new 5m windows
plus any currently open 15m/8h/1d windows.

```bash
mkdir -p /home/ubuntu/bin
cp deploy/scripts/market-aggregate-crypto.sh /home/ubuntu/bin/market-aggregate-crypto.sh
chmod +x /home/ubuntu/bin/market-aggregate-crypto.sh
(crontab -l 2>/dev/null | grep -v market-aggregate-crypto.sh; echo "*/5 * * * * /home/ubuntu/bin/market-aggregate-crypto.sh >> /home/ubuntu/github/mono/logs/crypto-aggregate.log 2>&1") | crontab -
```

## Aggregate Crypto Futures K Lines Every 5 Minutes

This job only reads local futures WebSocket 1m bars and incrementally upserts
higher intervals. It does not call Binance REST and it does not replace the
futures ranking sync.

```bash
mkdir -p /home/ubuntu/bin
cp deploy/scripts/market-aggregate-crypto-futures.sh /home/ubuntu/bin/market-aggregate-crypto-futures.sh
chmod +x /home/ubuntu/bin/market-aggregate-crypto-futures.sh
(crontab -l 2>/dev/null | grep -v market-aggregate-crypto-futures.sh; echo "*/5 * * * * /home/ubuntu/bin/market-aggregate-crypto-futures.sh >> /home/ubuntu/github/mono/logs/crypto-futures-aggregate.log 2>&1") | crontab -
```

## Sync Crypto Daily History

```bash
cp deploy/scripts/market-sync-crypto-daily.sh /home/ubuntu/bin/market-sync-crypto-daily.sh
chmod +x /home/ubuntu/bin/market-sync-crypto-daily.sh
/home/ubuntu/bin/market-sync-crypto-daily.sh
(crontab -l 2>/dev/null | grep -v market-sync-crypto-daily.sh; echo "20 2 * * * /home/ubuntu/bin/market-sync-crypto-daily.sh >> /home/ubuntu/github/mono/logs/crypto-daily-sync.log 2>&1") | crontab -
```

## Update Deployment

```bash
cd /home/ubuntu/github/mono
git pull
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
sudo systemctl restart market-api.service
sudo systemctl restart market-binance-kline-ws.service
sudo systemctl restart market-binance-futures-kline-ws.service
```

## PostgreSQL Cutover

PostgreSQL is the planned online database. SQLite remains the rollback source
until backup, row counts, and API validation all pass. Do not remove
`data/market.sqlite3` during cutover.

1. Install PostgreSQL and create an app database/user:

```bash
sudo apt-get update
sudo apt-get install -y postgresql postgresql-contrib
sudo -u postgres createuser market_app
sudo -u postgres createdb -O market_app market
sudo -u postgres psql -c "ALTER USER market_app WITH PASSWORD '<strong-password>';"
```

2. Back up the current SQLite database and verify it before any PostgreSQL work:

```bash
cd /home/ubuntu/github/mono
cp data/market.sqlite3 data/market.sqlite3.$(date +%Y%m%d%H%M%S).bak
sqlite3 data/market.sqlite3 "PRAGMA integrity_check;"
```

The integrity check must print `ok`. Stop if it does not.

3. Set the PostgreSQL connection string in `/home/ubuntu/github/mono/.market.env`:

```bash
MARKET_DATABASE_URL=postgresql://market_app:<strong-password>@127.0.0.1:5432/market
```

4. Run the additive, idempotent PostgreSQL initialization and backfill script:

```bash
/home/ubuntu/github/mono/deploy/scripts/market-backfill-postgres.sh
```

The script saves a SQLite row-count report under
`/home/ubuntu/github/mono/logs/sqlite-count-report.*.csv` before copying data.

5. Compare the saved SQLite count report with PostgreSQL row counts for every
online table. The counts must match before cutover.

6. Restart `market-api.service` only after counts match and after explicit
production cutover approval:

```bash
sudo systemctl restart market-api.service
```

7. Validate production reads:

```bash
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/boards/HK_STOCK_FOCUS20
curl -fsS "http://127.0.0.1:8000/api/bars/daily?market=HK&symbol=00700"
```

8. Roll back by removing or commenting out `MARKET_DATABASE_URL` in
`.market.env`, then restarting `market-api.service`. SQLite remains available
as the source of truth until PostgreSQL has passed validation.

## Firewall

Tencent Lighthouse firewall must allow:

```text
TCP 8000
```

For self-use, restrict the source IP when possible. If mobile IP changes frequently, keep `8000` open temporarily and switch to domain + HTTPS + auth later.
