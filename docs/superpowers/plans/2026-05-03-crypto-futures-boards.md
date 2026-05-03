# Crypto Futures Boards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Binance USD-M futures total and TradeFi turnover boards while keeping the existing spot crypto board unchanged.

**Architecture:** Add a focused futures data module that fetches Binance USD-M 24h tickers and exchange metadata, writes futures instruments/snapshots under `market='CRYPTO_FUTURES'`, then reuses existing `RankingRepository` and watchlist filtering for board rows. The sync path stays lightweight: no K-line REST pulls, no WebSocket subscriptions, no aggregation, and no schema migration.

**Tech Stack:** Python 3.11+, SQLite, existing `market` CLI, Binance USD-M REST endpoints `/fapi/v1/ticker/24hr` and `/fapi/v1/exchangeInfo`, existing vanilla JS frontend.

---

### Task 1: Futures Data Module

**Files:**
- Create: `src/market/binance_futures.py`
- Test: `tests/test_binance_futures.py`

- [ ] **Step 1: Write failing tests for futures parsing and selection**

Create `tests/test_binance_futures.py`:

```python
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from market.binance_futures import (
    FUTURES_TRADEFI_WATCHLIST,
    binance_futures_symbol_to_instrument,
    parse_binance_futures_24hr_ticker_snapshot,
    select_futures_tradefi_symbols,
    select_top_futures_usdt_symbols,
)
from market.db import connect, init_database
from market.models import Instrument
from market.repositories import InstrumentRepository


class BinanceFuturesTests(unittest.TestCase):
    def test_futures_instrument_uses_distinct_market_from_spot(self):
        instrument = binance_futures_symbol_to_instrument(
            {
                "symbol": "BTCUSDT",
                "pair": "BTCUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
                "underlyingType": "COIN",
                "underlyingSubType": ["PoW"],
            }
        )

        self.assertEqual(instrument.market, "CRYPTO_FUTURES")
        self.assertEqual(instrument.symbol, "BTCUSDT")
        self.assertEqual(instrument.instrument_type, "crypto_futures")
        self.assertEqual(instrument.quote_currency, "USDT")
        self.assertEqual(instrument.extra_meta["contract_type"], "PERPETUAL")
        self.assertEqual(instrument.extra_meta["underlying_sub_type"], ["PoW"])

    def test_futures_instrument_does_not_collide_with_spot_symbol(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                repository = InstrumentRepository(connection)
                spot_id = repository.upsert(
                    Instrument(
                        market="CRYPTO",
                        symbol="BTCUSDT",
                        display_name="BTCUSDT",
                        exchange="BINANCE",
                        instrument_type="crypto",
                        quote_currency="USDT",
                        timezone="UTC",
                    )
                )
                futures_id = repository.upsert(
                    binance_futures_symbol_to_instrument(
                        {
                            "symbol": "BTCUSDT",
                            "contractType": "PERPETUAL",
                            "status": "TRADING",
                            "quoteAsset": "USDT",
                            "marginAsset": "USDT",
                        }
                    )
                )

        self.assertNotEqual(spot_id, futures_id)

    def test_parse_futures_24hr_snapshot_uses_quote_volume(self):
        instrument = binance_futures_symbol_to_instrument(
            {"symbol": "COINUSDT", "quoteAsset": "USDT"}
        )
        snapshot = parse_binance_futures_24hr_ticker_snapshot(
            instrument_id=7,
            instrument=instrument,
            ticker={
                "symbol": "COINUSDT",
                "lastPrice": "255.50",
                "priceChangePercent": "4.25",
                "volume": "1000",
                "quoteVolume": "255500",
            },
            snapshot_ts_utc="2026-05-03T02:00:00Z",
            trade_date_local="2026-05-03",
        )

        self.assertEqual(snapshot.instrument_id, 7)
        self.assertEqual(snapshot.last_price, 255.5)
        self.assertEqual(snapshot.change_pct, 4.25)
        self.assertEqual(snapshot.volume_raw, 1000.0)
        self.assertEqual(snapshot.turnover_raw, 255500.0)
        self.assertEqual(snapshot.source, "binance_futures_24hr")

    def test_select_top_futures_usdt_symbols_filters_perpetual_trading_usdt(self):
        symbols = select_top_futures_usdt_symbols(
            [
                {"symbol": "OLDUSDT", "quoteVolume": "9999"},
                {"symbol": "BTCUSDT", "quoteVolume": "1000"},
                {"symbol": "ETHUSDT", "quoteVolume": "2000"},
                {"symbol": "BTCUSD_PERP", "quoteVolume": "999999"},
            ],
            {
                "OLDUSDT": {"contractType": "PERPETUAL", "status": "BREAK", "quoteAsset": "USDT"},
                "BTCUSDT": {"contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USDT"},
                "ETHUSDT": {"contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USDT"},
                "BTCUSD_PERP": {"contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USD"},
            },
            limit=2,
        )

        self.assertEqual(symbols, ["ETHUSDT", "BTCUSDT"])

    def test_select_futures_tradefi_symbols_uses_metadata_then_allowlist(self):
        symbols = select_futures_tradefi_symbols(
            [
                {"symbol": "COINUSDT", "quoteVolume": "100"},
                {"symbol": "BTCUSDT", "quoteVolume": "100000"},
                {"symbol": "MSTRUSDT", "quoteVolume": "50"},
            ],
            {
                "COINUSDT": {
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingSubType": ["TradFi"],
                },
                "BTCUSDT": {
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingSubType": ["PoW"],
                },
                "MSTRUSDT": {
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingSubType": [],
                },
            },
            limit=50,
        )

        self.assertEqual(symbols, ["COINUSDT"])

        fallback = select_futures_tradefi_symbols(
            [{"symbol": "MSTRUSDT", "quoteVolume": "50"}],
            {
                "MSTRUSDT": {
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingSubType": [],
                }
            },
            limit=50,
        )

        self.assertIn("MSTRUSDT", fallback)
        self.assertIn("MSTRUSDT", FUTURES_TRADEFI_WATCHLIST)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m unittest tests.test_binance_futures -v
```

Expected: FAIL or ERROR because `market.binance_futures` does not exist yet.

- [ ] **Step 3: Implement futures data helpers**

Create `src/market/binance_futures.py`:

```python
from __future__ import annotations

import json
from typing import Iterable
from urllib.request import Request, urlopen

from market.models import Instrument, MarketSnapshot

BINANCE_FUTURES_API_BASE = "https://fapi.binance.com"
FUTURES_TRADEFI_WATCHLIST = (
    "COINUSDT",
    "MSTRUSDT",
)


def fetch_binance_futures_24hr_tickers(
    *,
    base_url: str = BINANCE_FUTURES_API_BASE,
    timeout: float = 15.0,
) -> list[dict[str, object]]:
    request = Request(
        f"{base_url}/fapi/v1/ticker/24hr",
        headers={"User-Agent": "market-mvp/0.1"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("unexpected Binance futures 24hr ticker response")
    return [item for item in payload if isinstance(item, dict)]


def fetch_binance_futures_exchange_info(
    *,
    base_url: str = BINANCE_FUTURES_API_BASE,
    timeout: float = 15.0,
) -> dict[str, dict[str, object]]:
    request = Request(
        f"{base_url}/fapi/v1/exchangeInfo",
        headers={"User-Agent": "market-mvp/0.1"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("unexpected Binance futures exchangeInfo response")
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        return {}
    return {
        str(item["symbol"]).upper(): item
        for item in symbols
        if isinstance(item, dict) and item.get("symbol") is not None
    }


def binance_futures_symbol_to_instrument(symbol_info: dict[str, object]) -> Instrument:
    symbol = str(symbol_info.get("symbol", "")).upper()
    quote_asset = str(symbol_info.get("quoteAsset") or "USDT").upper()
    return Instrument(
        market="CRYPTO_FUTURES",
        symbol=symbol,
        display_name=symbol,
        exchange="BINANCE",
        instrument_type="crypto_futures",
        quote_currency=quote_asset,
        timezone="UTC",
        extra_meta={
            "contract_type": symbol_info.get("contractType"),
            "margin_asset": symbol_info.get("marginAsset"),
            "underlying_type": symbol_info.get("underlyingType"),
            "underlying_sub_type": symbol_info.get("underlyingSubType") or [],
        },
    )


def parse_binance_futures_24hr_ticker_snapshot(
    *,
    instrument_id: int,
    instrument: Instrument,
    ticker: dict[str, object],
    snapshot_ts_utc: str,
    trade_date_local: str,
) -> MarketSnapshot:
    return MarketSnapshot(
        instrument_id=instrument_id,
        snapshot_ts_utc=snapshot_ts_utc,
        trade_date_local=trade_date_local,
        last_price=_optional_float(ticker.get("lastPrice")),
        change_pct=_optional_float(ticker.get("priceChangePercent")),
        volume_raw=_optional_float(ticker.get("volume")),
        turnover_raw=_optional_float(ticker.get("quoteVolume")),
        quote_currency=instrument.quote_currency,
        source="binance_futures_24hr",
    )


def select_top_futures_usdt_symbols(
    tickers: list[dict[str, object]],
    exchange_info: dict[str, dict[str, object]],
    *,
    limit: int,
) -> list[str]:
    return _select_ranked_futures_symbols(
        tickers,
        exchange_info,
        limit=limit,
        allowed_symbols=None,
    )


def select_futures_tradefi_symbols(
    tickers: list[dict[str, object]],
    exchange_info: dict[str, dict[str, object]],
    *,
    limit: int,
) -> list[str]:
    metadata_symbols = {
        symbol
        for symbol, info in exchange_info.items()
        if _is_tradefi_symbol(info)
    }
    allowed_symbols = metadata_symbols or set(FUTURES_TRADEFI_WATCHLIST)
    return _select_ranked_futures_symbols(
        tickers,
        exchange_info,
        limit=limit,
        allowed_symbols=allowed_symbols,
    )


def _select_ranked_futures_symbols(
    tickers: list[dict[str, object]],
    exchange_info: dict[str, dict[str, object]],
    *,
    limit: int,
    allowed_symbols: set[str] | None,
) -> list[str]:
    rows: list[tuple[str, float]] = []
    for ticker in tickers:
        symbol = str(ticker.get("symbol", "")).upper()
        info = exchange_info.get(symbol, {})
        if allowed_symbols is not None and symbol not in allowed_symbols:
            continue
        if not _is_active_usdt_perpetual(info):
            continue
        rows.append((symbol, float(ticker.get("quoteVolume") or 0)))
    rows.sort(key=lambda row: (-row[1], row[0]))
    return [symbol for symbol, _quote_volume in rows[:limit]]


def _is_active_usdt_perpetual(symbol_info: dict[str, object]) -> bool:
    return (
        str(symbol_info.get("contractType", "")).upper() == "PERPETUAL"
        and str(symbol_info.get("status", "")).upper() == "TRADING"
        and str(symbol_info.get("quoteAsset", "")).upper() == "USDT"
    )


def _is_tradefi_symbol(symbol_info: dict[str, object]) -> bool:
    subtypes = symbol_info.get("underlyingSubType")
    if not isinstance(subtypes, Iterable) or isinstance(subtypes, (str, bytes)):
        return False
    return any(str(item).lower() in {"tradfi", "stock"} for item in subtypes)


def _optional_float(value) -> float | None:
    if value is None:
        return None
    return float(value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.venv/bin/python -m unittest tests.test_binance_futures -v
```

Expected: all `tests.test_binance_futures` tests pass.

- [ ] **Step 5: Commit Task 1**

Run:

```bash
git add src/market/binance_futures.py tests/test_binance_futures.py
git commit -m "Add Binance futures data helpers"
```

Expected: commit succeeds.

### Task 2: Futures Board Sync CLI

**Files:**
- Modify: `src/market/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI test**

Add this test to `tests/test_cli.py` near the other crypto board tests:

```python
    def test_sync_crypto_futures_boards_creates_total_and_tradefi_rankings(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()

            tickers = [
                {
                    "symbol": "BTCUSDT",
                    "lastPrice": "80000",
                    "priceChangePercent": "1",
                    "volume": "10",
                    "quoteVolume": "800000",
                },
                {
                    "symbol": "COINUSDT",
                    "lastPrice": "250",
                    "priceChangePercent": "2",
                    "volume": "1000",
                    "quoteVolume": "250000",
                },
                {
                    "symbol": "OLDUSDT",
                    "lastPrice": "1",
                    "priceChangePercent": "0",
                    "volume": "999999",
                    "quoteVolume": "999999",
                },
            ]
            exchange_info = {
                "BTCUSDT": {
                    "symbol": "BTCUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "underlyingSubType": ["PoW"],
                },
                "COINUSDT": {
                    "symbol": "COINUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "underlyingSubType": ["TradFi"],
                },
                "OLDUSDT": {
                    "symbol": "OLDUSDT",
                    "contractType": "PERPETUAL",
                    "status": "BREAK",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "underlyingSubType": ["TradFi"],
                },
            }

            with redirect_stdout(stdout), patch(
                "market.cli.fetch_binance_futures_24hr_tickers",
                return_value=tickers,
            ), patch(
                "market.cli.fetch_binance_futures_exchange_info",
                return_value=exchange_info,
            ):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "sync-crypto-futures-boards",
                        "--db-path",
                        str(db_path),
                        "--board-limit",
                        "50",
                        "--snapshot-ts-utc",
                        "2026-05-03T02:00:00Z",
                        "--trade-date-local",
                        "2026-05-03",
                    ]
                )

                with sqlite3.connect(db_path) as connection:
                    total_rows = connection.execute(
                        """
                        SELECT ranking_snapshot.rank, instrument.symbol
                        FROM ranking_snapshot
                        JOIN instrument
                            ON instrument.instrument_id = ranking_snapshot.instrument_id
                        WHERE ranking_snapshot.board_name = ?
                        ORDER BY ranking_snapshot.rank
                        """,
                        ("CRYPTO_FUTURES_TURNOVER_TOP50",),
                    ).fetchall()
                    tradefi_rows = connection.execute(
                        """
                        SELECT ranking_snapshot.rank, instrument.symbol
                        FROM ranking_snapshot
                        JOIN instrument
                            ON instrument.instrument_id = ranking_snapshot.instrument_id
                        WHERE ranking_snapshot.board_name = ?
                        ORDER BY ranking_snapshot.rank
                        """,
                        ("CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50",),
                    ).fetchall()
                    markets = connection.execute(
                        "SELECT DISTINCT market, instrument_type FROM instrument ORDER BY market"
                    ).fetchall()

        self.assertEqual(exit_code, 0)
        self.assertEqual(total_rows, [(1, "BTCUSDT"), (2, "COINUSDT")])
        self.assertEqual(tradefi_rows, [(1, "COINUSDT")])
        self.assertIn(("CRYPTO_FUTURES", "crypto_futures"), markets)
        self.assertIn("crypto futures boards synced: 3 tickers", stdout.getvalue())
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m unittest tests.test_cli.CliTests.test_sync_crypto_futures_boards_creates_total_and_tradefi_rankings -v
```

Expected: FAIL or ERROR because the command and imported futures helpers are not wired into `market.cli`.

- [ ] **Step 3: Wire futures sync command**

Modify `src/market/cli.py` imports:

```python
from market.binance_futures import (
    binance_futures_symbol_to_instrument,
    fetch_binance_futures_24hr_tickers,
    fetch_binance_futures_exchange_info,
    parse_binance_futures_24hr_ticker_snapshot,
    select_futures_tradefi_symbols,
    select_top_futures_usdt_symbols,
)
from market.repositories import (
    AlertEventRepository,
    AlertRuleRepository,
    InstrumentRepository,
    MarketSnapshotRepository,
    RankingRepository,
    WatchlistRepository,
)
from market.models import AlertRule, WatchlistEntry
```

Add parser after `sync-crypto-board`:

```python
    sync_crypto_futures = subparsers.add_parser(
        "sync-crypto-futures-boards",
        help="Sync Binance USD-M futures turnover boards",
    )
    sync_crypto_futures.add_argument("--db-path", type=Path, default=None)
    sync_crypto_futures.add_argument("--board-limit", type=int, default=50)
    sync_crypto_futures.add_argument("--snapshot-ts-utc", default=None)
    sync_crypto_futures.add_argument("--trade-date-local", default=None)
    sync_crypto_futures.add_argument("--dry-run", action="store_true")
```

Add command branch before `sync-crypto-board`:

```python
    if args.command == "sync-crypto-futures-boards":
        now = datetime.now(tz=UTC)
        snapshot_ts_utc = args.snapshot_ts_utc or now.strftime("%Y-%m-%dT%H:%M:%SZ")
        trade_date_local = args.trade_date_local or now.date().isoformat()
        if args.dry_run:
            print(
                "crypto futures board sync ready: "
                f"limit={args.board_limit} snapshot={snapshot_ts_utc}"
            )
            return 0
        tickers = fetch_binance_futures_24hr_tickers()
        exchange_info = fetch_binance_futures_exchange_info()
        with connect(db_path) as connection:
            result = run_collector_job(
                connection,
                job_name="sync-crypto-futures-boards",
                source_name="binance_futures",
                checkpoint=f"usd_m:{snapshot_ts_utc}:{args.board_limit}",
                started_at_utc=snapshot_ts_utc,
                operation=lambda: _sync_crypto_futures_boards(
                    connection,
                    tickers=tickers,
                    exchange_info=exchange_info,
                    snapshot_ts_utc=snapshot_ts_utc,
                    trade_date_local=trade_date_local,
                    board_limit=args.board_limit,
                ),
            )
        print(
            "crypto futures boards synced: "
            f"{result.items_synced} tickers, snapshot={snapshot_ts_utc}"
        )
        return 0
```

Add helper near existing private helpers:

```python
def _sync_crypto_futures_boards(
    connection: sqlite3.Connection,
    *,
    tickers: list[dict[str, object]],
    exchange_info: dict[str, dict[str, object]],
    snapshot_ts_utc: str,
    trade_date_local: str,
    board_limit: int,
) -> CollectorResult:
    total_symbols = select_top_futures_usdt_symbols(
        tickers,
        exchange_info,
        limit=board_limit,
    )
    tradefi_symbols = select_futures_tradefi_symbols(
        tickers,
        exchange_info,
        limit=board_limit,
    )
    symbols_to_store = sorted(set(total_symbols) | set(tradefi_symbols))
    ticker_by_symbol = {str(item.get("symbol", "")).upper(): item for item in tickers}

    instruments = InstrumentRepository(connection)
    snapshots = MarketSnapshotRepository(connection)
    instrument_ids_by_symbol: dict[str, int] = {}
    for symbol in symbols_to_store:
        info = exchange_info.get(symbol)
        ticker = ticker_by_symbol.get(symbol)
        if info is None or ticker is None:
            continue
        instrument = binance_futures_symbol_to_instrument(info)
        instrument_id = instruments.upsert(instrument)
        instrument_ids_by_symbol[symbol] = instrument_id
        snapshots.upsert(
            parse_binance_futures_24hr_ticker_snapshot(
                instrument_id=instrument_id,
                instrument=instrument,
                ticker=ticker,
                snapshot_ts_utc=snapshot_ts_utc,
                trade_date_local=trade_date_local,
            )
        )

    WatchlistRepository(connection).replace(
        "CRYPTO_FUTURES_TRADFI",
        [
            WatchlistEntry(
                instrument_id=instrument_ids_by_symbol[symbol],
                sort_order=index,
            )
            for index, symbol in enumerate(tradefi_symbols, start=1)
            if symbol in instrument_ids_by_symbol
        ],
    )
    ranking = RankingRepository(connection)
    total_count = ranking.refresh_turnover_board(
        board_name="CRYPTO_FUTURES_TURNOVER_TOP50",
        snapshot_ts_utc=snapshot_ts_utc,
        trade_date_local=trade_date_local,
        market="CRYPTO_FUTURES",
        instrument_type="crypto_futures",
        limit=board_limit,
    )
    tradefi_count = ranking.refresh_turnover_board(
        board_name="CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50",
        snapshot_ts_utc=snapshot_ts_utc,
        trade_date_local=trade_date_local,
        market="CRYPTO_FUTURES",
        instrument_type="crypto_futures",
        limit=board_limit,
        watchlist_name="CRYPTO_FUTURES_TRADFI",
    )
    return CollectorResult(
        source_name="binance_futures",
        items_synced=len(tickers),
        metadata={
            "stored_symbols": symbols_to_store,
            "total_board_rows": total_count,
            "tradefi_board_rows": tradefi_count,
        },
    )
```

- [ ] **Step 4: Run CLI test to verify it passes**

Run:

```bash
.venv/bin/python -m unittest tests.test_cli.CliTests.test_sync_crypto_futures_boards_creates_total_and_tradefi_rankings -v
```

Expected: PASS.

- [ ] **Step 5: Run related tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_cli tests.test_ranking_repository tests.test_binance_futures -v
```

Expected: all listed tests pass.

- [ ] **Step 6: Commit Task 2**

Run:

```bash
git add src/market/cli.py tests/test_cli.py
git commit -m "Add crypto futures board sync command"
```

Expected: commit succeeds.

### Task 3: Frontend Board Tabs

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing static asset test**

Add to `tests/test_api.py` near existing static frontend tests:

```python
    def test_get_static_asset_returns_crypto_futures_board_tabs(self):
        asset = get_static_asset("index.html")
        script = get_static_asset("app.js")

        self.assertIn(b'data-board="CRYPTO_TURNOVER_TOP50"', asset.body)
        self.assertIn(b'data-board="CRYPTO_FUTURES_TURNOVER_TOP50"', asset.body)
        self.assertIn(b'data-board="CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50"', asset.body)
        self.assertIn(b'CRYPTO_FUTURES_TURNOVER_TOP50: "Futures"', script.body)
        self.assertIn(b'CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50: "TradeFi"', script.body)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m unittest tests.test_api.ApiTests.test_get_static_asset_returns_crypto_futures_board_tabs -v
```

Expected: FAIL because futures tabs do not exist yet.

- [ ] **Step 3: Add frontend board entries**

Modify `frontend/index.html` tabs:

```html
<button class="tab" type="button" data-board="CRYPTO_TURNOVER_TOP50">Crypto</button>
<button class="tab" type="button" data-board="CRYPTO_FUTURES_TURNOVER_TOP50">Futures</button>
<button class="tab" type="button" data-board="CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50">TradeFi</button>
```

Modify `frontend/app.js` board labels:

```javascript
const BOARD_LABELS = {
  CRYPTO_TURNOVER_TOP50: "Crypto",
  CRYPTO_FUTURES_TURNOVER_TOP50: "Futures",
  CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50: "TradeFi",
};
```

- [ ] **Step 4: Run frontend asset test**

Run:

```bash
.venv/bin/python -m unittest tests.test_api.ApiTests.test_get_static_asset_returns_crypto_futures_board_tabs -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

Run:

```bash
git add frontend/index.html frontend/app.js tests/test_api.py
git commit -m "Add futures board tabs"
```

Expected: commit succeeds.

### Task 4: Deployment Script And Docs

**Files:**
- Create: `deploy/scripts/market-sync-crypto-futures.sh`
- Modify: `tests/test_deploy_config.py`
- Modify: `docs/deploy_tencent_lighthouse.md`
- Modify: `README.md`

- [ ] **Step 1: Write failing deploy script test**

Modify `tests/test_deploy_config.py` script list:

```python
        scripts = [
            REPO_ROOT / "deploy" / "scripts" / "market-run-binance-kline-ws.sh",
            REPO_ROOT / "deploy" / "scripts" / "market-sync-crypto.sh",
            REPO_ROOT / "deploy" / "scripts" / "market-sync-crypto-futures.sh",
            REPO_ROOT / "deploy" / "scripts" / "market-aggregate-crypto.sh",
            REPO_ROOT / "deploy" / "scripts" / "market-fill-crypto-gaps.sh",
        ]
```

- [ ] **Step 2: Run deploy test to verify it fails**

Run:

```bash
.venv/bin/python -m unittest tests.test_deploy_config.DeployConfigTests.test_deploy_scripts_used_by_systemd_are_executable -v
```

Expected: FAIL because `deploy/scripts/market-sync-crypto-futures.sh` does not exist.

- [ ] **Step 3: Add futures sync script**

Create `deploy/scripts/market-sync-crypto-futures.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
DB_PATH=${MARKET_DB_PATH:-$APP_DIR/data/market.sqlite3}
BOARD_LIMIT=${MARKET_FUTURES_BOARD_LIMIT:-50}

cd "$APP_DIR"

.venv/bin/market sync-crypto-futures-boards \
  --db-path "$DB_PATH" \
  --board-limit "$BOARD_LIMIT"
```

Run:

```bash
chmod +x deploy/scripts/market-sync-crypto-futures.sh
```

- [ ] **Step 4: Document cron setup**

Add to `docs/deploy_tencent_lighthouse.md` after the spot crypto sync section:

````markdown
## Sync Crypto Futures Boards Every 15 Minutes

This job refreshes Binance USD-M futures 24h snapshots and ranking boards only.
It does not pull K lines, subscribe to WebSockets, or aggregate local bars.

```bash
mkdir -p /home/ubuntu/bin
cp deploy/scripts/market-sync-crypto-futures.sh /home/ubuntu/bin/market-sync-crypto-futures.sh
chmod +x /home/ubuntu/bin/market-sync-crypto-futures.sh
(crontab -l 2>/dev/null | grep -v market-sync-crypto-futures.sh; echo "*/15 * * * * /home/ubuntu/bin/market-sync-crypto-futures.sh >> /home/ubuntu/github/mono/logs/crypto-futures-sync.log 2>&1") | crontab -
```
````

Add to `README.md` near the existing crypto board command:

````markdown
Sync Binance USD-M futures boards without K-line pulls:

```bash
.venv/bin/market sync-crypto-futures-boards \
  --db-path ./data/market.sqlite3 \
  --board-limit 50
```

This command refreshes `CRYPTO_FUTURES_TURNOVER_TOP50` and
`CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50`.
````

- [ ] **Step 5: Run deploy test**

Run:

```bash
.venv/bin/python -m unittest tests.test_deploy_config -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

Run:

```bash
git add deploy/scripts/market-sync-crypto-futures.sh tests/test_deploy_config.py docs/deploy_tencent_lighthouse.md README.md
git commit -m "Document crypto futures board sync"
```

Expected: commit succeeds.

### Task 5: Final Verification And Deployment

**Files:**
- No code changes expected.

- [ ] **Step 1: Run full local verification**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -v
git diff --check
git status --short
```

Expected: all tests pass, diff check has no output, and status is clean after committed tasks.

- [ ] **Step 2: Push branch**

Run:

```bash
git push origin codex-market-mvp
```

Expected: push succeeds.

- [ ] **Step 3: Deploy to Tencent**

Run:

```bash
ssh tencent-market 'cd /home/ubuntu/github/mono && git pull --ff-only && .venv/bin/python -m pip install -e . && .venv/bin/python -m unittest discover -s tests -v'
```

Expected: fast-forward to the pushed commit and all server tests pass.

- [ ] **Step 4: Install futures cron script and run once**

Run:

```bash
ssh tencent-market 'cd /home/ubuntu/github/mono && mkdir -p /home/ubuntu/bin logs && install -m 755 deploy/scripts/market-sync-crypto-futures.sh /home/ubuntu/bin/market-sync-crypto-futures.sh && (crontab -l 2>/dev/null | grep -v market-sync-crypto-futures.sh; echo "*/15 * * * * /home/ubuntu/bin/market-sync-crypto-futures.sh >> /home/ubuntu/github/mono/logs/crypto-futures-sync.log 2>&1") | crontab - && /home/ubuntu/bin/market-sync-crypto-futures.sh'
```

Expected: command prints `crypto futures boards synced: ... tickers, snapshot=...`.

- [ ] **Step 5: Verify board APIs on server**

Run:

```bash
ssh tencent-market 'cd /home/ubuntu/github/mono && .venv/bin/python - <<'"'"'PY'"'"'
import json
from urllib.request import urlopen

for board in [
    "CRYPTO_TURNOVER_TOP50",
    "CRYPTO_FUTURES_TURNOVER_TOP50",
    "CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50",
]:
    payload = json.loads(urlopen(f"http://127.0.0.1:8000/api/boards/{board}", timeout=10).read())
    print(board, len(payload["items"]), [item["symbol"] for item in payload["items"][:5]])
PY'
```

Expected: existing spot board still returns items, futures total board returns up to 50 items, TradeFi board returns the available TradeFi futures symbols.

- [ ] **Step 6: Verify cron and git state**

Run:

```bash
ssh tencent-market 'crontab -l 2>/dev/null || true'
ssh tencent-market 'cd /home/ubuntu/github/mono && git rev-parse --short HEAD && git status --short'
```

Expected: crontab includes `market-sync-crypto-futures.sh`, server HEAD is the pushed commit, and server worktree has no unexpected changes.

## Self-Review

- Spec coverage: the plan covers new futures data source, distinct market/instrument model, total futures board, TradeFi futures board with metadata and allowlist fallback, existing spot board preservation, frontend tabs, tests, and Tencent deployment.
- Placeholder scan: no placeholder markers or unspecified implementation steps are present.
- Type consistency: planned functions consistently use `CRYPTO_FUTURES`, `crypto_futures`, `CRYPTO_FUTURES_TURNOVER_TOP50`, and `CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50`.
