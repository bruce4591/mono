# Market MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-first market data MVP with SQLite storage, CLI jobs, read-only API, and a mobile PWA charting surface.

**Architecture:** Use one Python repository with separate collector/job and query/API processes sharing a single SQLite file. The first implementation slice creates the database foundation, typed repositories, and CLI commands before adding external collectors or the web UI.

**Tech Stack:** Python 3.11+, SQLite WAL, stdlib CLI for the first slice, pytest-compatible tests, future FastAPI and TradingView Lightweight Charts integration.

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

- [ ] Add a package under `src/market`.
- [ ] Keep runtime dependencies empty for the first slice.
- [ ] Add `.env.example` with `MARKET_DB_PATH` and `MARKET_LOG_LEVEL`.
- [ ] Add a CLI shell that supports `market --help`.

## Task 2: SQLite Schema Initialization

**Files:**
- Create: `src/market/db.py`
- Create: `src/market/schema.sql`
- Create: `tests/test_db_schema.py`

- [ ] Write failing tests that initialize a temporary SQLite database.
- [ ] Verify WAL mode is enabled.
- [ ] Verify required tables exist.
- [ ] Implement schema initialization with idempotent `CREATE TABLE IF NOT EXISTS`.

## Task 3: Instrument Repository

**Files:**
- Create: `src/market/models.py`
- Create: `src/market/repositories.py`
- Create: `tests/test_repositories.py`

- [ ] Write failing tests for idempotent instrument upsert.
- [ ] Implement `Instrument` and repository methods.
- [ ] Ensure duplicate `market + symbol` writes update existing rows.

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

- [ ] Write failing CLI tests for `init-db` and `health-check`.
- [ ] Implement commands using stdlib `argparse`.
- [ ] Ensure commands accept `--db-path`.

## Task 6: Next Slice Plan

**Files:**
- Modify: `docs/superpowers/plans/2026-04-25-market-mvp.md`

- [x] Add static `config/watchlists/*.json` loading for ETF Focus20 and Index Focus20.
- [x] Add snapshot and ranking repositories to build turnover top boards from `market_snapshot`.
- [x] Add fake collector data seeding for local snapshots, daily bars, and intraday bars.
- [ ] Add collector interfaces with fake clients first, then AKShare and Binance implementations.
- [x] Add first read-only API route for boards.
- [x] Add the first mobile Web/PWA screen for boards.
- [x] Add read-only API routes for instruments and bars.
- [x] Add local K-line chart preview with interval, volume, and turnover fields.
- [ ] Add read-only API routes for watchlists, health, and jobs.
- [ ] Add mobile PWA pages for rankings, instrument detail, K line chart, watchlists, and system status.
- [ ] Add later-stage indicator and alert modules after the data and K line MVP is usable.
