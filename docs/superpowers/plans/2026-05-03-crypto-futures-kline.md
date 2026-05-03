# Crypto Futures Kline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Crypto a top-level area with Crypto, Futures, and TradeFi sub-tabs, and add Binance USD-M futures historical K-lines, 1m WebSocket ingestion, and local aggregation.

**Architecture:** Reuse the existing spot crypto storage model and APIs, adding futures-specific data adapters where source URLs, market identity, and source names differ. Futures and TradeFi both use `market='CRYPTO_FUTURES'`; TradeFi remains a filtered futures board and does not store separate K-line data.

**Tech Stack:** Python stdlib, SQLite, existing `market` package repositories, Binance Spot REST/WS, Binance USD-M Futures REST/WS, vanilla frontend JavaScript, `unittest`, systemd, cron.

---

## File Map

- Modify `frontend/index.html`: replace peer Futures/TradeFi top-level tabs with Crypto sub-tabs.
- Modify `frontend/app.js`: represent top-level ETF/Crypto state and nested Crypto board state.
- Modify `frontend/styles.css`: style nested Crypto sub-tabs using existing tab language.
- Modify `tests/test_api.py`: assert frontend assets expose nested Crypto sub-tabs.
- Modify `src/market/binance_futures.py`: add USD-M futures K-line REST fetch, parse, and range sync helpers.
- Modify `tests/test_binance_futures.py`: cover futures K-line parsing and range sync storage.
- Modify `src/market/crypto_gaps.py`: generalize gap filling to support `CRYPTO` and `CRYPTO_FUTURES`.
- Modify `src/market/api.py`: route `CRYPTO_FUTURES` intraday short-window fills to futures gap fill.
- Modify `tests/test_crypto_gap_fill.py`: cover futures gap fill without touching spot bars.
- Modify `tests/test_api.py`: cover futures detail API gap fill.
- Modify `src/market/realtime.py`: add futures kline event parsing/apply helpers.
- Modify `src/market/collectors/binance_ws.py`: support configurable WebSocket base URL, source label, instrument market, and gap filler.
- Modify `src/market/cli.py`: add `run-binance-futures-kline-ws` and `aggregate-crypto-futures-klines`.
- Modify `tests/test_realtime.py`, `tests/test_binance_ws.py`, and `tests/test_cli.py`: cover futures WS and CLI paths.
- Modify `src/market/aggregators.py`: add market-aware aggregation wrapper.
- Modify `tests/test_aggregators.py`: prove futures aggregation writes futures bars and leaves spot bars unchanged.
- Create `deploy/scripts/market-run-binance-futures-kline-ws.sh`: run futures WS with reconnect gap fill.
- Create `deploy/scripts/market-aggregate-crypto-futures.sh`: run futures aggregation.
- Create `deploy/systemd/market-binance-futures-kline-ws.service`: systemd unit for futures WS.
- Modify `deploy/scripts/market-sync-crypto-futures.sh`: leave ranking sync unchanged; no K-line work here.
- Modify `tests/test_deploy_config.py`: cover executable deploy scripts and service settings.
- Modify `docs/deploy_tencent_lighthouse.md` and `README.md`: document futures K-line deployment and no scheduled gap fill.

---

## Task 1: Crypto Nested Tabs UI

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing asset test**

Add or update `ApiTests.test_get_static_asset_returns_crypto_futures_board_tabs`:

```python
def test_get_static_asset_returns_crypto_futures_board_tabs(self):
    asset = get_static_asset("/index.html")
    script = get_static_asset("/app.js")

    self.assertIn(b'data-section="ETF"', asset.body)
    self.assertIn(b'data-section="CRYPTO"', asset.body)
    self.assertIn(b'id="cryptoSubtabs"', asset.body)
    self.assertIn(b'data-board="CRYPTO_TURNOVER_TOP50"', asset.body)
    self.assertIn(b'data-board="CRYPTO_FUTURES_TURNOVER_TOP50"', asset.body)
    self.assertIn(b'data-board="CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50"', asset.body)
    self.assertIn(b'const CRYPTO_BOARDS =', script.body)
    self.assertIn(b'CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50: "TradeFi"', script.body)
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python -m unittest tests.test_api.ApiTests.test_get_static_asset_returns_crypto_futures_board_tabs -v
```

Expected: FAIL because `data-section` and `cryptoSubtabs` are not present.

- [ ] **Step 3: Update the markup**

Change `frontend/index.html` tab area to this structure:

```html
<nav class="tabs" aria-label="市场切换">
  <button class="tab is-active" type="button" data-section="ETF" data-board="ETF_FOCUS20">ETF</button>
  <button class="tab" type="button" data-section="CRYPTO" data-board="CRYPTO_TURNOVER_TOP50">Crypto</button>
</nav>

<nav class="subtabs is-hidden" id="cryptoSubtabs" aria-label="Crypto 榜单切换">
  <button class="subtab is-active" type="button" data-board="CRYPTO_TURNOVER_TOP50">Crypto</button>
  <button class="subtab" type="button" data-board="CRYPTO_FUTURES_TURNOVER_TOP50">Futures</button>
  <button class="subtab" type="button" data-board="CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50">TradeFi</button>
</nav>
```

- [ ] **Step 4: Update frontend state**

In `frontend/app.js`, replace the flat tab handling with:

```javascript
const BOARD_LABELS = {
  ETF_FOCUS20: "ETF",
  CRYPTO_TURNOVER_TOP50: "Crypto",
  CRYPTO_FUTURES_TURNOVER_TOP50: "Futures",
  CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50: "TradeFi",
};

const CRYPTO_BOARDS = [
  "CRYPTO_TURNOVER_TOP50",
  "CRYPTO_FUTURES_TURNOVER_TOP50",
  "CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50",
];

let activeSection = "ETF";
let activeBoard = "ETF_FOCUS20";
const sectionTabs = Array.from(document.querySelectorAll(".tab"));
const cryptoSubtabs = document.querySelector("#cryptoSubtabs");
const cryptoBoardTabs = Array.from(document.querySelectorAll(".subtab"));

function sectionForBoard(board) {
  return CRYPTO_BOARDS.includes(board) ? "CRYPTO" : "ETF";
}

function syncNavigation(board) {
  activeBoard = board;
  activeSection = sectionForBoard(board);
  sectionTabs.forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.section === activeSection);
  });
  cryptoSubtabs.classList.toggle("is-hidden", activeSection !== "CRYPTO");
  cryptoBoardTabs.forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.board === activeBoard);
  });
}
```

Update `loadBoard` to call `syncNavigation(board)` before fetching. Replace existing tab listeners with:

```javascript
sectionTabs.forEach((tab) => {
  tab.addEventListener("click", () => loadBoard(tab.dataset.board));
});

cryptoBoardTabs.forEach((tab) => {
  tab.addEventListener("click", () => loadBoard(tab.dataset.board));
});
```

- [ ] **Step 5: Add CSS for nested tabs**

Add to `frontend/styles.css`:

```css
.subtabs {
  display: flex;
  gap: 8px;
  margin-top: -10px;
  margin-bottom: 14px;
  overflow-x: auto;
}

.subtabs.is-hidden {
  display: none;
}

.subtab {
  border: 1px solid #26323d;
  background: #111820;
  color: #8fa3b4;
  border-radius: 8px;
  padding: 8px 12px;
  font: inherit;
  white-space: nowrap;
}

.subtab.is-active {
  border-color: #0ecb81;
  color: #e8edf2;
}
```

- [ ] **Step 6: Verify and commit**

Run:

```bash
.venv/bin/python -m unittest tests.test_api.ApiTests.test_get_static_asset_returns_crypto_futures_board_tabs -v
```

Expected: PASS.

Commit:

```bash
git add frontend/index.html frontend/app.js frontend/styles.css tests/test_api.py
git commit -m "Nest futures boards under crypto tab"
```

---

## Task 2: Futures REST K-line Helpers

**Files:**
- Modify: `src/market/binance_futures.py`
- Test: `tests/test_binance_futures.py`

- [ ] **Step 1: Write failing futures K-line tests**

Add imports:

```python
from market.binance_futures import (
    fetch_binance_futures_klines_range,
    parse_binance_futures_kline,
    sync_binance_futures_klines_range,
)
from market.repositories import IntradayBarRepository
```

Add tests:

```python
def test_parse_futures_kline_uses_futures_instrument_id(self):
    bar = parse_binance_futures_kline(
        instrument_id=42,
        interval="1m",
        row=[
            1777000000000,
            "2300.10",
            "2308.00",
            "2299.50",
            "2307.25",
            "12.5",
            1777000059999,
            "28840.625",
        ],
        timezone_name="UTC",
    )

    self.assertEqual(bar.instrument_id, 42)
    self.assertEqual(bar.interval, "1m")
    self.assertEqual(bar.bar_start_ts_utc, "2026-04-24T03:06:40Z")
    self.assertEqual(bar.bar_end_ts_utc, "2026-04-24T03:07:40Z")
    self.assertEqual(bar.high, 2308.0)
    self.assertEqual(bar.turnover_raw, 28840.625)
    self.assertEqual(bar.source, "binance_futures")


def test_sync_futures_klines_range_writes_crypto_futures_bars(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "market.sqlite3"
        init_database(db_path)

        def fetcher(symbol, interval, start_time_ms, end_time_ms, limit):
            self.assertEqual(symbol, "ETHUSDT")
            self.assertEqual(interval, "1m")
            return [[1777000000000, "1", "2", "0.5", "1.5", "10", 1777000059999, "15"]]

        with connect(db_path) as connection:
            result = sync_binance_futures_klines_range(
                connection,
                symbol="ETHUSDT",
                interval="1m",
                start_time_ms=1777000000000,
                end_time_ms=1777000059999,
                limit=1,
                fetcher=fetcher,
            )
            instrument = InstrumentRepository(connection).get_by_market_symbol(
                "CRYPTO_FUTURES",
                "ETHUSDT",
            )
            bars = IntradayBarRepository(connection).list_for_instrument(
                instrument.instrument_id,
                "1m",
            )

    self.assertEqual(result.bars, 1)
    self.assertEqual(instrument.instrument_type, "crypto_futures")
    self.assertEqual(bars[0].source, "binance_futures_gap_fill")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m unittest tests.test_binance_futures -v
```

Expected: FAIL because futures K-line functions are missing.

- [ ] **Step 3: Implement futures REST helpers**

Add to `src/market/binance_futures.py`:

```python
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from market.models import Instrument, IntradayBar, MarketSnapshot
from market.repositories import InstrumentRepository, IntradayBarRepository

FuturesRangeKlineFetcher = Callable[[str, str, int, int, int], list[list[object]]]


@dataclass(frozen=True)
class BinanceFuturesSyncResult:
    symbol: str
    interval: str
    bars: int
    latest_close: float | None


def fetch_binance_futures_klines_range(
    symbol: str,
    interval: str,
    start_time_ms: int,
    end_time_ms: int,
    limit: int,
    *,
    base_url: str = BINANCE_FUTURES_API_BASE,
    timeout: float = 15.0,
) -> list[list[object]]:
    query = urlencode(
        {
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
            "limit": limit,
        }
    )
    request = Request(
        f"{base_url}/fapi/v1/klines?{query}",
        headers={"User-Agent": "market-mvp/0.1"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("unexpected Binance futures kline response")
    return payload


def parse_binance_futures_kline(
    *,
    instrument_id: int,
    interval: str,
    row: list[object],
    timezone_name: str,
) -> IntradayBar:
    open_time_ms = int(row[0])
    close_time_ms = int(row[6])
    timezone = ZoneInfo(timezone_name)
    local_start = datetime.fromtimestamp(open_time_ms / 1000, tz=UTC).astimezone(timezone)
    return IntradayBar(
        instrument_id=instrument_id,
        interval=interval,
        bar_start_ts_utc=_format_utc_ms(open_time_ms),
        bar_end_ts_utc=_format_utc_ms(close_time_ms + 1),
        trade_date_local=local_start.date().isoformat(),
        open=_optional_float(row[1]),
        high=_optional_float(row[2]),
        low=_optional_float(row[3]),
        close=_optional_float(row[4]),
        volume_raw=_optional_float(row[5]),
        turnover_raw=_optional_float(row[7]) if len(row) > 7 else None,
        is_closed_bar=close_time_ms < int(datetime.now(tz=UTC).timestamp() * 1000),
        source="binance_futures",
    )


def sync_binance_futures_klines_range(
    connection,
    *,
    symbol: str,
    interval: str,
    start_time_ms: int,
    end_time_ms: int,
    limit: int,
    now_ms: int | None = None,
    source: str = "binance_futures_gap_fill",
    fetcher: FuturesRangeKlineFetcher = fetch_binance_futures_klines_range,
) -> BinanceFuturesSyncResult:
    normalized_symbol = symbol.upper()
    instrument = binance_futures_symbol_to_instrument({"symbol": normalized_symbol})
    instrument_id = InstrumentRepository(connection).upsert(instrument)
    rows = fetcher(normalized_symbol, interval, start_time_ms, end_time_ms, limit)
    resolved_now_ms = now_ms or int(datetime.now(tz=UTC).timestamp() * 1000)
    bars = [
        replace(
            parse_binance_futures_kline(
                instrument_id=instrument_id,
                interval=interval,
                row=row,
                timezone_name=instrument.timezone,
            ),
            is_closed_bar=int(row[6]) < resolved_now_ms,
            source=source,
        )
        for row in rows
    ]
    repository = IntradayBarRepository(connection)
    for bar in bars:
        repository.upsert(bar)
    latest = bars[-1] if bars else None
    return BinanceFuturesSyncResult(
        symbol=normalized_symbol,
        interval=interval,
        bars=len(bars),
        latest_close=latest.close if latest else None,
    )


def _format_utc_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
```

- [ ] **Step 4: Verify and commit**

Run:

```bash
.venv/bin/python -m unittest tests.test_binance_futures -v
```

Expected: PASS.

Commit:

```bash
git add src/market/binance_futures.py tests/test_binance_futures.py
git commit -m "Add futures kline REST helpers"
```

---

## Task 3: Futures Detail API Gap Fill

**Files:**
- Modify: `src/market/aggregators.py`
- Modify: `src/market/crypto_gaps.py`
- Modify: `src/market/api.py`
- Test: `tests/test_aggregators.py`
- Test: `tests/test_crypto_gap_fill.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing gap fill unit test**

Add to `tests/test_crypto_gap_fill.py`:

```python
from market.binance_futures import binance_futures_symbol_to_instrument
from market.crypto_gaps import fill_binance_futures_1m_gaps


def test_fill_binance_futures_1m_gaps_writes_futures_market_only(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "market.sqlite3"
        init_database(db_path)
        calls = []

        def fetcher(symbol, interval, start_time_ms, end_time_ms, limit):
            calls.append((symbol, interval, start_time_ms, end_time_ms, limit))
            return [[_ms("2026-05-03T00:01:00Z"), "10", "12", "9", "11", "2", _ms("2026-05-03T00:01:59.999Z"), "22"]]

        with connect(db_path) as connection:
            futures_id = InstrumentRepository(connection).upsert(
                binance_futures_symbol_to_instrument({"symbol": "ETHUSDT"})
            )
            IntradayBarRepository(connection).upsert(
                _bar(
                    futures_id,
                    start="2026-05-03T00:00:00Z",
                    end="2026-05-03T00:01:00Z",
                    close=10.0,
                )
            )

            result = fill_binance_futures_1m_gaps(
                connection,
                symbols=["ETHUSDT"],
                start_ts_utc="2026-05-03T00:00:00Z",
                end_ts_utc="2026-05-03T00:02:00Z",
                fetcher=fetcher,
                now_ms=_ms("2026-05-03T00:03:00Z"),
            )

            row = connection.execute(
                """
                SELECT instrument.market, bar_intraday.source, bar_intraday.close
                FROM bar_intraday
                JOIN instrument ON instrument.instrument_id = bar_intraday.instrument_id
                WHERE instrument.symbol = 'ETHUSDT'
                    AND bar_intraday.bar_start_ts_utc = '2026-05-03T00:01:00Z'
                """
            ).fetchone()

    self.assertEqual(result.gaps_filled, 1)
    self.assertEqual(row, ("CRYPTO_FUTURES", "binance_futures_gap_fill", 11.0))
```

- [ ] **Step 2: Write failing market aggregation test**

Add to `tests/test_aggregators.py`:

```python
from market.aggregators import aggregate_market_from_1m
from market.binance_futures import binance_futures_symbol_to_instrument


def test_aggregate_market_from_1m_writes_futures_without_touching_spot(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "market.sqlite3"
        init_database(db_path)

        with connect(db_path) as connection:
            spot_id = _seed_btc_1m_bars(connection, minutes=5)
            futures_id = InstrumentRepository(connection).upsert(
                binance_futures_symbol_to_instrument({"symbol": "BTCUSDT"})
            )
            repository = IntradayBarRepository(connection)
            for index in range(5):
                repository.upsert(
                    IntradayBar(
                        instrument_id=futures_id,
                        interval="1m",
                        bar_start_ts_utc=f"2026-04-12T14:0{index}:00Z",
                        bar_end_ts_utc=f"2026-04-12T14:0{index + 1}:00Z",
                        trade_date_local="2026-04-12",
                        open=200.0 + index,
                        high=201.0 + index,
                        low=199.0 + index,
                        close=200.5 + index,
                        volume_raw=1.0,
                        turnover_raw=200.0,
                        is_closed_bar=True,
                        source="binance_futures_ws_kline",
                    )
                )

            result = aggregate_market_from_1m(
                connection,
                market="CRYPTO_FUTURES",
                symbols=["BTCUSDT"],
            )
            spot_5m = IntradayBarRepository(connection).list_for_instrument(spot_id, "5m")
            futures_5m = IntradayBarRepository(connection).list_for_instrument(futures_id, "5m")

    self.assertGreater(result.bars_written, 0)
    self.assertEqual(spot_5m, [])
    self.assertEqual(futures_5m[0].source, "aggregate_1m")
```

- [ ] **Step 3: Write failing API test**

Add to `tests/test_api.py`:

```python
def test_get_intraday_bars_payload_backfills_futures_window_when_missing(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "market.sqlite3"
        init_database(db_path)
        calls = []

        def fetcher(symbol, interval, start_time_ms, end_time_ms, limit):
            calls.append((symbol, interval, limit))
            return [[_ms("2026-05-03T00:00:00Z"), "1", "2", "0.5", "1.5", "10", _ms("2026-05-03T00:00:59.999Z"), "15"]]

        with connect(db_path) as connection:
            InstrumentRepository(connection).upsert(
                binance_futures_symbol_to_instrument({"symbol": "ETHUSDT"})
            )
            payload = get_intraday_bars_payload(
                connection,
                "CRYPTO_FUTURES",
                "ETHUSDT",
                "1m",
                now_ts_utc="2026-05-03T00:01:00Z",
                gap_fetcher=fetcher,
                gap_min_request_interval_seconds=0,
            )

    self.assertEqual(payload["market"], "CRYPTO_FUTURES")
    self.assertEqual(payload["items"][0]["source"], "binance_futures_gap_fill")
    self.assertEqual(calls[0][0], "ETHUSDT")
```

- [ ] **Step 4: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m unittest tests.test_crypto_gap_fill.CryptoGapFillTests.test_fill_binance_futures_1m_gaps_writes_futures_market_only tests.test_aggregators.AggregatorTests.test_aggregate_market_from_1m_writes_futures_without_touching_spot tests.test_api.ApiTests.test_get_intraday_bars_payload_backfills_futures_window_when_missing -v
```

Expected: FAIL because futures gap fill and market-aware aggregation are missing.

- [ ] **Step 5: Add market-aware aggregation**

In `src/market/aggregators.py`, add:

```python
def aggregate_market_from_1m(
    connection: sqlite3.Connection,
    *,
    market: str,
    symbols: list[str],
) -> AggregationResult:
    if not symbols:
        return AggregationResult(bars_written=0)
    placeholders = ",".join("?" for _symbol in symbols)
    rows = connection.execute(
        f"""
        SELECT instrument_id
        FROM instrument
        WHERE market = ?
            AND symbol IN ({placeholders})
        ORDER BY symbol
        """,
        [market, *[symbol.upper() for symbol in symbols]],
    ).fetchall()
    instrument_ids = [int(row["instrument_id"]) for row in rows]
    intraday = aggregate_intraday_from_1m(
        connection,
        instrument_ids=instrument_ids,
        target_intervals=["5m", "15m", "8h"],
    )
    daily = aggregate_daily_from_intraday(
        connection,
        instrument_ids=instrument_ids,
        source_interval="1m",
    )
    return AggregationResult(bars_written=intraday.bars_written + daily.bars_written)
```

Change `aggregate_crypto_from_1m` to:

```python
def aggregate_crypto_from_1m(
    connection: sqlite3.Connection,
    symbols: list[str],
) -> AggregationResult:
    return aggregate_market_from_1m(connection, market="CRYPTO", symbols=symbols)
```

- [ ] **Step 6: Generalize gap fill**

Add to `src/market/crypto_gaps.py`:

```python
from market.aggregators import aggregate_crypto_from_1m, aggregate_market_from_1m
from market.binance_futures import (
    FuturesRangeKlineFetcher,
    binance_futures_symbol_to_instrument,
    fetch_binance_futures_klines_range,
    sync_binance_futures_klines_range,
)


def fill_binance_futures_1m_gaps(
    connection: sqlite3.Connection,
    *,
    symbols: list[str],
    start_ts_utc: str,
    end_ts_utc: str,
    now_ms: int | None = None,
    fetcher: FuturesRangeKlineFetcher = fetch_binance_futures_klines_range,
    min_request_interval_seconds: float = BINANCE_GAP_FILL_MIN_REQUEST_INTERVAL_SECONDS,
) -> GapFillResult:
    return _fill_binance_1m_gaps_for_market(
        connection,
        symbols=symbols,
        start_ts_utc=start_ts_utc,
        end_ts_utc=end_ts_utc,
        now_ms=now_ms,
        fetcher=fetcher,
        min_request_interval_seconds=min_request_interval_seconds,
        instrument_factory=lambda symbol: binance_futures_symbol_to_instrument({"symbol": symbol}),
        range_sync=sync_binance_futures_klines_range,
        aggregate=lambda conn, syms: aggregate_market_from_1m(
            conn,
            market="CRYPTO_FUTURES",
            symbols=syms,
        ),
    )
```

Refactor existing `fill_binance_1m_gaps` to call `_fill_binance_1m_gaps_for_market` with spot factories. Keep the public spot function signature unchanged.

- [ ] **Step 7: Update API backfill dispatch**

In `src/market/api.py`, import futures fill:

```python
from market.crypto_gaps import (
    BINANCE_KLINES_MAX_LIMIT,
    fill_binance_1m_gaps,
    fill_binance_futures_1m_gaps,
)
```

Change the backfill branch:

```python
if market in {"CRYPTO", "CRYPTO_FUTURES"} and should_backfill:
    _ensure_crypto_intraday_window(
        connection,
        market=market,
        symbol=symbol,
        interval=interval,
        before_ts_utc=before_ts_utc,
        now_ts_utc=now_ts_utc,
        fetcher=gap_fetcher,
        min_request_interval_seconds=gap_min_request_interval_seconds,
    )
```

Update `_ensure_crypto_intraday_window` signature to include `market: str`, and dispatch:

```python
fill_func = fill_binance_futures_1m_gaps if market == "CRYPTO_FUTURES" else fill_binance_1m_gaps
fill_func(
    connection,
    symbols=[symbol],
    start_ts_utc=_format_utc(start),
    end_ts_utc=_format_utc(end),
    fetcher=fetcher,
    min_request_interval_seconds=min_request_interval_seconds,
)
```

- [ ] **Step 8: Verify and commit**

Run:

```bash
.venv/bin/python -m unittest tests.test_aggregators tests.test_crypto_gap_fill tests.test_api.ApiTests.test_get_intraday_bars_payload_backfills_futures_window_when_missing -v
```

Expected: PASS.

Commit:

```bash
git add src/market/aggregators.py src/market/crypto_gaps.py src/market/api.py tests/test_aggregators.py tests/test_crypto_gap_fill.py tests/test_api.py
git commit -m "Fill futures kline gaps on demand"
```

---

## Task 4: Futures WebSocket Ingestion

**Files:**
- Modify: `src/market/realtime.py`
- Modify: `src/market/collectors/binance_ws.py`
- Modify: `src/market/cli.py`
- Test: `tests/test_realtime.py`
- Test: `tests/test_binance_ws.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing realtime test**

Add to `tests/test_realtime.py`:

```python
from market.realtime import apply_binance_futures_kline_event


def test_apply_binance_futures_kline_event_writes_futures_bar_and_snapshot(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "market.sqlite3"
        init_database(db_path)
        payload = {
            "stream": "ethusdt@kline_1m",
            "data": {
                "E": 1777000005000,
                "s": "ETHUSDT",
                "k": {
                    "s": "ETHUSDT",
                    "i": "1m",
                    "t": 1777000000000,
                    "T": 1777000059999,
                    "o": "1",
                    "h": "2",
                    "l": "0.5",
                    "c": "1.5",
                    "v": "10",
                    "q": "15",
                    "x": False,
                },
            },
        }

        with connect(db_path) as connection:
            bar = apply_binance_futures_kline_event(connection, payload, aggregate=False)
            row = connection.execute(
                """
                SELECT instrument.market, bar_intraday.source, market_snapshot.source
                FROM bar_intraday
                JOIN instrument ON instrument.instrument_id = bar_intraday.instrument_id
                JOIN market_snapshot ON market_snapshot.instrument_id = instrument.instrument_id
                WHERE instrument.symbol = 'ETHUSDT'
                """
            ).fetchone()

    self.assertEqual(bar.source, "binance_futures_ws_kline")
    self.assertEqual(row, ("CRYPTO_FUTURES", "binance_futures_ws_kline", "binance_futures_ws_kline_price"))
```

- [ ] **Step 2: Write failing CLI dry-run test**

Add to `tests/test_cli.py`:

```python
def test_run_binance_futures_kline_ws_is_registered(self):
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "run-binance-futures-kline-ws",
                "--symbol",
                "ETHUSDT",
                "--dry-run",
            ]
        )

    self.assertEqual(exit_code, 0)
    self.assertIn("binance futures kline ws ready: ETHUSDT interval=1m", stdout.getvalue())
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m unittest tests.test_realtime.RealtimeTests.test_apply_binance_futures_kline_event_writes_futures_bar_and_snapshot tests.test_cli.CliTests.test_run_binance_futures_kline_ws_is_registered -v
```

Expected: FAIL because futures realtime and CLI paths are missing.

- [ ] **Step 4: Implement futures realtime apply helper**

In `src/market/realtime.py`, add:

```python
from market.aggregators import aggregate_market_from_1m
from market.binance_futures import binance_futures_symbol_to_instrument


def apply_binance_futures_kline_event(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    aggregate: bool = True,
) -> IntradayBar:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("Binance futures kline payload must be an object")
    kline = data.get("k")
    if not isinstance(kline, dict):
        raise ValueError("Binance futures kline payload must include kline object")

    symbol = str(kline.get("s") or data["s"]).upper()
    event_time_ms = int(data["E"])
    instrument = binance_futures_symbol_to_instrument({"symbol": symbol})
    instrument_id = InstrumentRepository(connection).upsert(instrument)
    bar = parse_binance_kline_event(
        payload,
        instrument_id=instrument_id,
        timezone_name=instrument.timezone,
    )
    bar = replace(bar, source="binance_futures_ws_kline")
    IntradayBarRepository(connection).upsert(bar)
    if aggregate and bar.interval == "1m":
        aggregate_market_from_1m(connection, market="CRYPTO_FUTURES", symbols=[symbol])
    _upsert_kline_price_snapshot(
        connection,
        instrument_id=instrument_id,
        snapshot_ts_utc=_format_utc_ms(event_time_ms),
        trade_date_local=bar.trade_date_local,
        last_price=bar.close,
        fallback_volume_raw=bar.volume_raw,
        fallback_turnover_raw=bar.turnover_raw,
        quote_currency=instrument.quote_currency,
        source="binance_futures_ws_kline_price",
    )
    return bar
```

Update `_upsert_kline_price_snapshot` to accept `source: str = "binance_ws_kline_price"` and use that value in `MarketSnapshot`.

- [ ] **Step 5: Make collector configurable**

In `src/market/collectors/binance_ws.py`, add constructor parameters:

```python
ws_base_url: str = BINANCE_WS_BASE_URL
log_prefix: str = "binance ws"
message_handler=apply_binance_kline_event
```

Use `ws_base_url` in `build_combined_kline_stream_urls`. Replace hard-coded log strings with `self.log_prefix`, for example:

```python
self._log(f"{self.log_prefix} connecting: url={_redact_stream_url(url)}")
```

Use `self.message_handler(connection, payload, aggregate=False)` in `_on_message`.

- [ ] **Step 6: Add futures CLI command**

In `src/market/cli.py`, add parser:

```python
run_futures_kline_ws = subparsers.add_parser(
    "run-binance-futures-kline-ws",
    help="Run Binance USD-M futures combined WebSocket 1m kline collector",
)
run_futures_kline_ws.add_argument("--db-path", type=Path, default=None)
run_futures_kline_ws.add_argument("--symbol", action="append", default=[])
run_futures_kline_ws.add_argument("--interval", default="1m")
run_futures_kline_ws.add_argument("--max-streams-per-connection", type=int, default=200)
run_futures_kline_ws.add_argument("--top-usdt-limit", type=int, default=60)
run_futures_kline_ws.add_argument("--gap-fill-on-reconnect", action="store_true")
run_futures_kline_ws.add_argument("--dry-run", action="store_true")
```

Add command handling:

```python
def _fill_binance_futures_1m_gaps_for_ws(connection, symbols, start_ts_utc, end_ts_utc):
    fill_binance_futures_1m_gaps(
        connection,
        symbols=symbols,
        start_ts_utc=start_ts_utc,
        end_ts_utc=end_ts_utc,
    )


if args.command == "run-binance-futures-kline-ws":
    if args.symbol:
        normalized_symbols = [symbol.upper() for symbol in args.symbol]
    else:
        tickers = fetch_binance_futures_24hr_tickers()
        exchange_info = fetch_binance_futures_exchange_info()
        normalized_symbols = select_top_futures_usdt_symbols(
            tickers,
            exchange_info,
            limit=args.top_usdt_limit,
        )
    if args.dry_run:
        print(
            "binance futures kline ws ready: "
            f"{','.join(normalized_symbols) or 'none'} interval={args.interval}"
        )
        return 0
    collector = BinanceKlineWebSocketCollector(
        db_path=db_path,
        symbols=normalized_symbols,
        interval=args.interval,
        max_streams_per_connection=args.max_streams_per_connection,
        ws_base_url="wss://fstream.binance.com",
        log_prefix="binance futures ws",
        message_handler=apply_binance_futures_kline_event,
        gap_fill_on_reconnect=args.gap_fill_on_reconnect,
        gap_filler=_fill_binance_futures_1m_gaps_for_ws,
    )
    result = collector.run_forever()
    print(f"binance futures kline ws stopped: {result.items_synced} messages, interval={args.interval}")
    return 0
```

- [ ] **Step 7: Verify and commit**

Run:

```bash
.venv/bin/python -m unittest tests.test_realtime tests.test_binance_ws tests.test_cli.CliTests.test_run_binance_futures_kline_ws_is_registered -v
```

Expected: PASS.

Commit:

```bash
git add src/market/realtime.py src/market/collectors/binance_ws.py src/market/cli.py tests/test_realtime.py tests/test_binance_ws.py tests/test_cli.py
git commit -m "Add futures kline websocket ingestion"
```

---

## Task 5: Futures Aggregation CLI

**Files:**
- Modify: `src/market/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI test**

Add to `tests/test_cli.py`:

```python
def test_aggregate_crypto_futures_klines_is_registered(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "market.sqlite3"
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            main(["init-db", "--db-path", str(db_path)])
            exit_code = main(
                [
                    "aggregate-crypto-futures-klines",
                    "--db-path",
                    str(db_path),
                    "--symbol",
                    "ETHUSDT",
                    "--dry-run",
                ]
            )

    self.assertEqual(exit_code, 0)
    self.assertIn("crypto futures aggregate ready: ETHUSDT", stdout.getvalue())
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m unittest tests.test_cli.CliTests.test_aggregate_crypto_futures_klines_is_registered -v
```

Expected: FAIL because the futures aggregation CLI command is missing.

- [ ] **Step 3: Implement futures aggregation CLI**

In `src/market/cli.py`, add parser:

```python
aggregate_futures = subparsers.add_parser(
    "aggregate-crypto-futures-klines",
    help="Aggregate local Binance USD-M futures 1m bars into higher intervals",
)
aggregate_futures.add_argument("--db-path", type=Path, default=None)
aggregate_futures.add_argument("--symbol", action="append", default=[])
aggregate_futures.add_argument("--top-usdt-limit", type=int, default=60)
aggregate_futures.add_argument("--dry-run", action="store_true")
```

Add command handling:

```python
if args.command == "aggregate-crypto-futures-klines":
    if args.symbol:
        symbols = [symbol.upper() for symbol in args.symbol]
    else:
        tickers = fetch_binance_futures_24hr_tickers()
        exchange_info = fetch_binance_futures_exchange_info()
        symbols = select_top_futures_usdt_symbols(tickers, exchange_info, limit=args.top_usdt_limit)
    if args.dry_run:
        print(f"crypto futures aggregate ready: {','.join(symbols) or 'none'}")
        return 0
    with connect(db_path) as connection:
        result = aggregate_market_from_1m(
            connection,
            market="CRYPTO_FUTURES",
            symbols=symbols,
        )
    print(f"crypto futures aggregate complete: {result.bars_written} bars")
    return 0
```

- [ ] **Step 4: Verify and commit**

Run:

```bash
.venv/bin/python -m unittest tests.test_cli.CliTests.test_aggregate_crypto_futures_klines_is_registered tests.test_aggregators -v
```

Expected: PASS.

Commit:

```bash
git add src/market/cli.py tests/test_cli.py
git commit -m "Aggregate futures klines locally"
```

---

## Task 6: Futures Deployment Artifacts And Final Verification

**Files:**
- Create: `deploy/scripts/market-run-binance-futures-kline-ws.sh`
- Create: `deploy/scripts/market-aggregate-crypto-futures.sh`
- Create: `deploy/systemd/market-binance-futures-kline-ws.service`
- Modify: `tests/test_deploy_config.py`
- Modify: `README.md`
- Modify: `docs/deploy_tencent_lighthouse.md`

- [ ] **Step 1: Write failing deploy config tests**

Add to `tests/test_deploy_config.py`:

```python
def test_binance_futures_ws_service_uses_top_usdt_limit_and_gap_fill(self):
    service = (
        REPO_ROOT / "deploy" / "systemd" / "market-binance-futures-kline-ws.service"
    ).read_text(encoding="utf-8")
    script = (
        REPO_ROOT / "deploy" / "scripts" / "market-run-binance-futures-kline-ws.sh"
    ).read_text(encoding="utf-8")

    self.assertIn("Environment=MARKET_FUTURES_WS_TOP_USDT_LIMIT=60", service)
    self.assertIn("run-binance-futures-kline-ws", script)
    self.assertIn("--gap-fill-on-reconnect", script)
```

Update `test_deploy_scripts_used_by_systemd_are_executable` to include:

```python
REPO_ROOT / "deploy" / "scripts" / "market-run-binance-futures-kline-ws.sh",
REPO_ROOT / "deploy" / "scripts" / "market-aggregate-crypto-futures.sh",
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m unittest tests.test_deploy_config -v
```

Expected: FAIL because futures deploy files are missing.

- [ ] **Step 3: Add futures WS run script**

Create `deploy/scripts/market-run-binance-futures-kline-ws.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
DB_PATH=${MARKET_DB_PATH:-$APP_DIR/data/market.sqlite3}
SYMBOLS=${MARKET_FUTURES_WS_SYMBOLS:-}
TOP_USDT_LIMIT=${MARKET_FUTURES_WS_TOP_USDT_LIMIT:-60}

cd "$APP_DIR"

args=()
for symbol in $SYMBOLS; do
  args+=(--symbol "$symbol")
done
if [[ ${#args[@]} -eq 0 ]]; then
  args+=(--top-usdt-limit "$TOP_USDT_LIMIT")
fi

.venv/bin/market run-binance-futures-kline-ws \
  --db-path "$DB_PATH" \
  "${args[@]}" \
  --interval 1m \
  --gap-fill-on-reconnect
```

Run:

```bash
chmod +x deploy/scripts/market-run-binance-futures-kline-ws.sh
```

- [ ] **Step 4: Add futures aggregation script**

Create `deploy/scripts/market-aggregate-crypto-futures.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MARKET_APP_DIR:-/home/ubuntu/github/mono}
DB_PATH=${MARKET_DB_PATH:-$APP_DIR/data/market.sqlite3}
TOP_USDT_LIMIT=${MARKET_FUTURES_AGGREGATE_TOP_USDT_LIMIT:-60}

cd "$APP_DIR"

.venv/bin/market aggregate-crypto-futures-klines \
  --db-path "$DB_PATH" \
  --top-usdt-limit "$TOP_USDT_LIMIT"
```

Run:

```bash
chmod +x deploy/scripts/market-aggregate-crypto-futures.sh
```

- [ ] **Step 5: Add futures systemd service**

Create `deploy/systemd/market-binance-futures-kline-ws.service`:

```ini
[Unit]
Description=Market Binance USD-M futures 1m kline WebSocket collector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/ubuntu/github/mono
Environment=MARKET_APP_DIR=/home/ubuntu/github/mono
Environment=MARKET_DB_PATH=/home/ubuntu/github/mono/data/market.sqlite3
Environment=MARKET_FUTURES_WS_TOP_USDT_LIMIT=60
ExecStart=/home/ubuntu/github/mono/deploy/scripts/market-run-binance-futures-kline-ws.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 6: Update docs**

Add to `README.md` and `docs/deploy_tencent_lighthouse.md`:

````markdown
### Binance USD-M Futures Kline Service

The futures ranking sync remains lightweight and does not fetch K-lines. Futures
K-lines are handled by a separate WebSocket service plus local aggregation:

```bash
sudo cp deploy/systemd/market-binance-futures-kline-ws.service /etc/systemd/system/market-binance-futures-kline-ws.service
sudo systemctl daemon-reload
sudo systemctl enable --now market-binance-futures-kline-ws.service
```

Aggregate local futures 1m bars every 5 minutes:

```cron
*/5 * * * * /home/ubuntu/bin/market-aggregate-crypto-futures.sh >> /home/ubuntu/github/mono/logs/crypto-futures-aggregate.log 2>&1
```

No scheduled futures REST gap fill is installed. Missing futures bars are filled
only by detail API windows or WebSocket reconnect gap checks.
````

- [ ] **Step 7: Verify locally and commit**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Expected: all tests PASS and diff check emits no output.

Commit:

```bash
git add deploy/scripts/market-run-binance-futures-kline-ws.sh deploy/scripts/market-aggregate-crypto-futures.sh deploy/systemd/market-binance-futures-kline-ws.service tests/test_deploy_config.py README.md docs/deploy_tencent_lighthouse.md
git commit -m "Add futures kline deployment artifacts"
```

- [ ] **Step 8: Deploy to Tencent**

Run:

```bash
git push origin codex-market-mvp
ssh tencent-market 'cd /home/ubuntu/github/mono && git pull --ff-only && .venv/bin/python -m pip install -e . && .venv/bin/python -m unittest discover -s tests -v'
ssh tencent-market 'cd /home/ubuntu/github/mono && sudo cp deploy/systemd/market-binance-futures-kline-ws.service /etc/systemd/system/market-binance-futures-kline-ws.service && sudo systemctl daemon-reload && sudo systemctl enable --now market-binance-futures-kline-ws.service && sudo systemctl restart market-binance-futures-kline-ws.service'
ssh tencent-market 'cd /home/ubuntu/github/mono && install -m 755 deploy/scripts/market-aggregate-crypto-futures.sh /home/ubuntu/bin/market-aggregate-crypto-futures.sh && (crontab -l 2>/dev/null | grep -v market-aggregate-crypto-futures.sh; echo "*/5 * * * * /home/ubuntu/bin/market-aggregate-crypto-futures.sh >> /home/ubuntu/github/mono/logs/crypto-futures-aggregate.log 2>&1") | crontab - && /home/ubuntu/bin/market-aggregate-crypto-futures.sh'
```

Expected:

- Server tests PASS.
- `market-binance-futures-kline-ws.service` is active.
- Futures aggregation command exits 0.

- [ ] **Step 9: Verify live futures K-line APIs**

Run:

```bash
ssh tencent-market 'cd /home/ubuntu/github/mono && .venv/bin/python - <<'"'"'PY'"'"'
import json
import urllib.request

for symbol in ("ETHUSDT", "COINUSDT"):
    for interval in ("1m", "5m", "15m"):
        url = f"http://127.0.0.1:8000/api/bars/intraday?market=CRYPTO_FUTURES&symbol={symbol}&interval={interval}&limit=5"
        with urllib.request.urlopen(url, timeout=20) as response:
            payload = json.load(response)
        print(symbol, interval, len(payload["items"]), payload["items"][-1]["bar_start_ts_utc"] if payload["items"] else None)
PY'
```

Expected:

- `ETHUSDT 1m` returns at least 1 item.
- `COINUSDT 1m` returns at least 1 item after API-driven REST fill if it is not in the active WS top 60.
- `5m` and `15m` return items after aggregation has run with local 1m bars.

---

## Final Verification Checklist

- [ ] `Crypto` top-level tab reveals `Crypto / Futures / TradeFi` sub-tabs.
- [ ] `Futures` rows link to `/instrument.html?market=CRYPTO_FUTURES&symbol=...`.
- [ ] `TradeFi` rows link to `/instrument.html?market=CRYPTO_FUTURES&symbol=...`.
- [ ] Futures REST K-line gap fill uses `/fapi/v1/klines`.
- [ ] Futures WebSocket uses `wss://fstream.binance.com`.
- [ ] Futures 1m bars use source `binance_futures_ws_kline` or `binance_futures_gap_fill`.
- [ ] Futures snapshots from WS kline use `binance_futures_ws_kline_price`.
- [ ] Futures aggregation writes `5m`, `15m`, `8h`, and daily bars.
- [ ] No scheduled futures REST gap-fill cron exists.
- [ ] Existing spot crypto tests still pass.
