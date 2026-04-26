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

## Sync Crypto Every 15 Minutes

```bash
mkdir -p /home/ubuntu/bin
cp deploy/scripts/market-sync-crypto.sh /home/ubuntu/bin/market-sync-crypto.sh
chmod +x /home/ubuntu/bin/market-sync-crypto.sh
(crontab -l 2>/dev/null | grep -v market-sync-crypto.sh; echo "*/15 * * * * /home/ubuntu/bin/market-sync-crypto.sh >> /home/ubuntu/github/mono/logs/crypto-sync.log 2>&1") | crontab -
```

## Update Deployment

```bash
cd /home/ubuntu/github/mono
git pull
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
sudo systemctl restart market-api.service
```

## Firewall

Tencent Lighthouse firewall must allow:

```text
TCP 8000
```

For self-use, restrict the source IP when possible. If mobile IP changes frequently, keep `8000` open temporarily and switch to domain + HTTPS + auth later.
