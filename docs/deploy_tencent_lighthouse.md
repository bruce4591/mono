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
```

## Firewall

Tencent Lighthouse firewall must allow:

```text
TCP 8000
```

For self-use, restrict the source IP when possible. If mobile IP changes frequently, keep `8000` open temporarily and switch to domain + HTTPS + auth later.
