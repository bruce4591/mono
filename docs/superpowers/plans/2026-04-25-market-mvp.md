# Market MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-first market data MVP with SQLite storage, CLI jobs, read-only API, and a mobile PWA charting surface.

**Architecture:** Use one Python repository with separate collector/job and query/API processes sharing a single SQLite file. The first implementation slice creates the database foundation, typed repositories, and CLI commands before adding external collectors or the web UI.

**Tech Stack:** Python 3.11+, SQLite WAL, stdlib CLI, stdlib HTTP API, unittest-compatible tests, mobile Web/PWA, KLineCharts.

---

## Task 1: Project Skeleton And Local Configuration

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `src/market/__init__.py`
- Create: `src/market/settings.py`
- Create: `src/market/cli.py`
- Create: `tests/test_settings.py`

- [x] Add a package under `src/market`.
- [x] Keep runtime dependencies empty for the first slice.
- [x] Add `.env.example` with `MARKET_DB_PATH` and `MARKET_LOG_LEVEL`.
- [x] Add a CLI shell that supports `market --help`.

## Task 2: SQLite Schema Initialization

**Files:**
- Create: `src/market/db.py`
- Create: `src/market/schema.sql`
- Create: `tests/test_db_schema.py`

- [x] Write failing tests that initialize a temporary SQLite database.
- [x] Verify WAL mode is enabled.
- [x] Verify required tables exist.
- [x] Implement schema initialization with idempotent `CREATE TABLE IF NOT EXISTS`.

## Task 3: Instrument Repository

**Files:**
- Create: `src/market/models.py`
- Create: `src/market/repositories.py`
- Create: `tests/test_repositories.py`

- [x] Write failing tests for idempotent instrument upsert.
- [x] Implement `Instrument` and repository methods.
- [x] Ensure duplicate `market + symbol` writes update existing rows.

## Task 4: Watchlists And Bar Repositories

**Files:**
- Modify: `src/market/models.py`
- Modify: `src/market/repositories.py`
- Create: `tests/test_bar_repositories.py`
- Create: `tests/test_watchlist_repository.py`

- [x] Add `DailyBar` and `DailyBarRepository` with idempotent `(instrument_id, trade_date)` upsert.
- [x] Add `IntradayBar` and `IntradayBarRepository` with idempotent `(instrument_id, interval, bar_start_ts_utc)` upsert.
- [x] Add `WatchlistEntry` and `WatchlistRepository.replace()` for Focus20-style active list replacement.

## Task 5: CLI Jobs For Database Smoke Tests

**Files:**
- Modify: `src/market/cli.py`
- Create: `tests/test_cli.py`

- [x] Write failing CLI tests for `init-db` and `health-check`.
- [x] Implement commands using stdlib `argparse`.
- [x] Ensure commands accept `--db-path`.

## Task 6: Next Slice Plan

**Files:**
- Modify: `docs/superpowers/plans/2026-04-25-market-mvp.md`

- [x] Add static `config/watchlists/*.json` loading for ETF Focus20 and Index Focus20.
- [x] Add snapshot and ranking repositories to build turnover top boards from `market_snapshot`.
- [x] Add fake collector data seeding for local snapshots, daily bars, and intraday bars.
- [x] Add collector interfaces with fake clients first, then AKShare and Binance implementations.
- [x] Add first read-only API route for boards.
- [x] Add the first mobile Web/PWA screen for boards.
- [x] Add read-only API routes for instruments and bars.
- [x] Add local K-line chart preview with interval, volume, and turnover fields.
- [x] Add read-only API routes for watchlists, health, and jobs.
- [x] Add mobile PWA pages for rankings, instrument detail, K line chart, watchlists, and system status.
- [x] Add later-stage indicator and alert modules after the data and K line MVP is usable.

---

## Current MVP Status

The deployed MVP is usable on Tencent Lighthouse over port `8000`.

Completed:

- SQLite schema, WAL initialization, and idempotent repositories.
- CLI jobs for database initialization, health checks, watchlist sync, sample data, Binance crypto sync, ranking refresh, and combined crypto board sync.
- Static ETF and index focus watchlists.
- Binance crypto 15m K-line sync for `BTCUSDT` and `ETHUSDT`.
- Binance crypto 1m K-line WebSocket collector with local aggregation and gap fill.
- Turnover ranking snapshots.
- Read-only API for boards, instruments, bars, watchlists, health, and jobs.
- Mobile Web/PWA pages for rankings, instrument detail, KLineCharts K-line chart, and system status.
- Tencent Lighthouse deployment with systemd API and Binance K-line WebSocket services, plus cron-based crypto sync.

Not yet complete:

- AKShare collectors for A-share, HK, US, ETF, and index daily/60m data.
- Full technical indicator library beyond snapshot-based alert metrics.
- Authentication or IP restriction for long-term public mobile access.

---

## Recommended Next Development Order

### Slice 1: Indicator And Alert MVP

**Goal:** Add a minimal backend alert system on top of the existing bars and snapshots, then show triggered alerts in the mobile status page.

**Why first:** This matches the intended personal-use workflow: watch market changes, see K-line context, and get notified or at least surfaced when a rule triggers. It does not require a new data vendor or large dependency.

**Files:**
- Create: `src/market/alerts.py`
- Create: `tests/test_alerts.py`
- Modify: `src/market/schema.sql`
- Modify: `src/market/repositories.py`
- Modify: `src/market/cli.py`
- Modify: `src/market/api.py`
- Modify: `frontend/status.html`
- Modify: `frontend/status.js`

**Data model:**
- `alert_rule`
  - `rule_id`
  - `name`
  - `market`
  - `symbol`
  - `metric`
  - `operator`
  - `threshold`
  - `is_active`
  - `created_at`
  - `updated_at`
- `alert_event`
  - `event_id`
  - `rule_id`
  - `instrument_id`
  - `triggered_at_utc`
  - `metric`
  - `observed_value`
  - `threshold`
  - `message`
  - `is_acknowledged`

**Initial supported alert metrics:**
- `change_pct`: latest snapshot涨跌幅超过阈值。
- `turnover_raw`: latest snapshot成交额超过阈值。
- `volume_raw`: latest snapshot成交量超过阈值。

**Tasks:**
- [x] Write failing tests for alert rule evaluation against latest `market_snapshot`.
- [x] Add alert tables with idempotent schema migration behavior.
- [x] Add repository methods to upsert/list rules and insert/list events.
- [x] Add CLI commands: `add-alert-rule`, `run-alerts`, `list-alert-events`.
- [x] Add API endpoints: `/api/alerts/rules` and `/api/alerts/events`.
- [x] Add alert section to `/status.html`.
- [x] Run local full tests.
- [x] Deploy to Tencent and verify alert events over `http://150.109.22.77:8000/api/alerts/events`.

**Current implementation note:** The alert framework is metric-registry based. Built-in metrics are `change_pct`, `turnover_raw`, `volume_raw`, and `last_price`; future indicators can be added by registering new metric resolvers without changing the rule/event tables.

### Slice 2: Unified Collector Interface

**Goal:** Standardize collectors before adding more data sources.

**Why second:** Current Binance code works, but AKShare will introduce multiple markets and different field names. A shared collector contract prevents each market from becoming special-case code.

**Files:**
- Create: `src/market/collectors/base.py`
- Create: `src/market/collectors/binance.py`
- Create: `tests/test_collectors.py`
- Modify: `src/market/binance.py`
- Modify: `src/market/cli.py`

**Collector contract:**
- `sync_universe(connection) -> SyncResult`
- `sync_daily_bars(connection, symbols, days) -> SyncResult`
- `sync_intraday_bars(connection, symbols, interval, limit) -> SyncResult`
- `sync_snapshots(connection, symbols) -> SyncResult`

**Tasks:**
- [x] Write failing tests for a fake collector implementing the contract.
- [x] Move Binance sync behind the collector interface without changing CLI behavior.
- [x] Keep `sync-crypto-board` compatible with current cron script.
- [x] Add job/source health updates at the collector boundary.
- [x] Run local and server tests.

**Collector safety policy:**
- [x] Binance collector rate-limits symbol requests with a default 1 second interval.
- [x] Binance REST kline policy is tied to the official `GET /api/v3/klines` request weight of `2` and a conservative local `120` request-weight/minute safety budget.
- [x] Binance crypto board defaults to USDT quoteVolume Top50 instead of a fixed BTC/ETH pair list.
- [x] Binance 365-day `1d` daily bars can be synced for one symbol or the USDT quoteVolume Top50.
- [x] Collector jobs do not retry failed requests by default.
- [x] Collector job failures are persisted to `job_state` and `source_health` before the exception is raised.
- [ ] Apply the same default rate-limit and no-retry behavior to the future AKShare collector.

**Realtime price direction:**
- [x] Keep historical K lines, turnover, and ranking refresh on REST/scheduled jobs.
- [x] Add a no-network Binance ticker event processor that updates `market_snapshot`.
- [x] Add a CLI entry point to apply one Binance ticker event JSON payload for smoke testing.
- [x] Poll the instrument detail snapshot every 5 seconds so latest price can update without reloading K lines.
- [x] Add a Binance WebSocket 1m K-line collector for realtime crypto K-line updates.
- [x] Respect Binance WS limits by using grouped combined streams, throttling subscribe/unsubscribe messages, and reconnecting before/after the 24-hour connection lifetime.
- [x] Keep board ranking refresh at minute-level cadence; do not tie ranking recompute to every realtime tick.
- [ ] Add a Binance WebSocket ticker/miniTicker collector if sub-minute latest-price updates are needed.

### Slice 3: AKShare Market Data MVP

**Goal:** Add first non-crypto market sync for ETF/index focus pools, then expand to stock boards.

**Why third:** AKShare is a new dependency and more likely to have source-format changes, so it should come after the collector boundary exists.

**Files:**
- Modify: `pyproject.toml`
- Create: `src/market/collectors/akshare.py`
- Create: `tests/test_akshare_normalizers.py`
- Modify: `src/market/cli.py`
- Modify: `deploy/scripts/market-sync-crypto.sh` or create `deploy/scripts/market-sync-daily.sh`

**Initial scope:**
- ETF Focus20 daily bars and snapshots.
- Index Focus20 daily bars and snapshots.
- Optional US ETF 60m only if AKShare source is stable enough.

**Tasks:**
- [x] Confirm adding `akshare` dependency before implementation.
- [x] Write normalizer tests with fixture rows, not live network calls.
- [x] Implement AKShare collector with live calls isolated behind small methods.
- [x] Add CLI command `sync-akshare-focus`.
- [x] Refresh `ETF_FOCUS20` and `INDEX_FOCUS20` boards after sync.
- [x] Add cron script for daily post-close sync.
- [x] Deploy and validate status page shows updated ETF/index board snapshots.

### Slice 4: Access Hardening For Mobile Use

**Goal:** Make personal mobile access safer while keeping deployment lightweight.

**Options:**
- Simple shared token in URL/header for API and pages.
- Nginx reverse proxy with basic auth.
- Domain plus HTTPS certificate after domain review/备案 is ready.

**Recommendation:** Start with a simple shared token or Nginx basic auth before opening more endpoints publicly.
