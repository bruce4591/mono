from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from market.collectors.akshare import (
    AKSHARE_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
    AkshareCollector,
    _instrument_by_id,
    fetch_akshare_daily_frame,
    parse_akshare_daily_frame,
)
from market.db import connect, init_database
from market.models import Instrument
from market.watchlists import sync_watchlist_from_file


class FakeFrame:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = records

    def to_dict(self, orient: str) -> list[dict[str, object]]:
        if orient != "records":
            raise ValueError("unexpected orient")
        return self.records


class _FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _FakeInstrumentConnection:
    def __init__(self, row):
        self.row = row

    def execute(self, sql, params=()):
        return _FakeResult(self.row)


class AkshareNormalizerTests(unittest.TestCase):
    def test_fetch_akshare_daily_frame_routes_by_market_and_type(self):
        calls = []

        class FakeAkshare:
            def stock_zh_a_hist(self, **kwargs):
                calls.append(("stock_zh_a_hist", kwargs))
                return FakeFrame([])

            def stock_hk_hist(self, **kwargs):
                calls.append(("stock_hk_hist", kwargs))
                return FakeFrame([])

            def stock_us_daily(self, **kwargs):
                calls.append(("stock_us_daily", kwargs))
                return FakeFrame([])

            def index_us_stock_sina(self, **kwargs):
                calls.append(("index_us_stock_sina", kwargs))
                return FakeFrame([])

            def futures_foreign_hist(self, **kwargs):
                calls.append(("futures_foreign_hist", kwargs))
                return FakeFrame([])

            def futures_global_hist_em(self, **kwargs):
                calls.append(("futures_global_hist_em", kwargs))
                return FakeFrame([])

        instruments = [
            Instrument("A_SHARE", "600519", "Kweichow Moutai", "SSE", "stock", "CNY", "Asia/Shanghai"),
            Instrument("HK", "00700", "Tencent", "HKEX", "stock", "HKD", "Asia/Hong_Kong"),
            Instrument("US", "AAPL", "Apple", "NASDAQ", "stock", "USD", "America/New_York"),
            Instrument("US", "SPX", "S&P 500 Index", "CBOE", "index", "USD", "America/New_York"),
            Instrument("CMDTY", "OIL", "Brent Oil", "SINA", "commodity", "USD", "UTC"),
            Instrument(
                "CMDTY",
                "RB00Y",
                "NYMEX Gasoline",
                "EM",
                "commodity",
                "USD",
                "UTC",
                extra_meta={"akshare_function": "futures_global_hist_em"},
            ),
        ]

        with patch("market.collectors.akshare._load_akshare", return_value=FakeAkshare()):
            for instrument in instruments:
                fetch_akshare_daily_frame(instrument)

        self.assertEqual(
            calls,
            [
                ("stock_zh_a_hist", {"symbol": "600519", "period": "daily", "adjust": ""}),
                ("stock_hk_hist", {"symbol": "00700", "period": "daily", "adjust": ""}),
                ("stock_us_daily", {"symbol": "AAPL", "adjust": ""}),
                ("index_us_stock_sina", {"symbol": ".INX"}),
                ("futures_foreign_hist", {"symbol": "OIL"}),
                ("futures_global_hist_em", {"symbol": "RB00Y"}),
            ],
        )

    def test_parse_akshare_daily_frame_normalizes_daily_rows(self):
        bars = parse_akshare_daily_frame(
            instrument_id=7,
            frame=FakeFrame(
                [
                    {
                        "date": "2026-04-23",
                        "open": "100.0",
                        "high": "105.0",
                        "low": "99.0",
                        "close": "104.0",
                        "volume": "1000",
                        "amount": "104000",
                    },
                    {
                        "date": "2026-04-24",
                        "open": "104.0",
                        "high": "108.0",
                        "low": "103.0",
                        "close": "106.0",
                        "volume": "1500",
                        "amount": "",
                    },
                ]
            ),
            quote_currency="USD",
            source="akshare",
        )

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].trade_date, "2026-04-23")
        self.assertEqual(bars[0].turnover_raw, 104000.0)
        self.assertEqual(bars[1].close, 106.0)
        self.assertEqual(bars[1].volume_raw, 1500.0)
        self.assertEqual(bars[1].turnover_raw, 159000.0)
        self.assertEqual(bars[1].quote_currency, "USD")
        self.assertEqual(bars[1].source, "akshare")

    def test_parse_akshare_daily_frame_accepts_global_futures_columns(self):
        bars = parse_akshare_daily_frame(
            instrument_id=9,
            frame=FakeFrame(
                [
                    {
                        "日期": "2026-05-01",
                        "开盘": "3.50",
                        "最高": "3.70",
                        "最低": "3.40",
                        "最新价": "3.61",
                        "总量": "47318",
                    }
                ]
            ),
            quote_currency="USD",
            source="akshare",
        )

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].trade_date, "2026-05-01")
        self.assertEqual(bars[0].close, 3.61)
        self.assertEqual(bars[0].volume_raw, 47318.0)
        self.assertEqual(bars[0].turnover_raw, 3.61 * 47318)

    def test_instrument_by_id_accepts_postgres_jsonb_dict_extra_meta(self):
        connection = _FakeInstrumentConnection(
            {
                "instrument_id": 9,
                "market": "CMDTY",
                "symbol": "RB00Y",
                "display_name": "NYMEX Gasoline",
                "exchange": "EM",
                "instrument_type": "commodity",
                "quote_currency": "USD",
                "timezone": "UTC",
                "is_active": True,
                "extra_meta": {"akshare_function": "futures_global_hist_em"},
            }
        )

        instrument = _instrument_by_id(connection, 9)

        assert instrument is not None
        self.assertEqual(
            instrument.extra_meta,
            {"akshare_function": "futures_global_hist_em"},
        )

    def test_akshare_collector_syncs_focus_watchlists_with_rate_limit(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            etf_config = Path(tmp_dir) / "etf.json"
            index_config = Path(tmp_dir) / "index.json"
            etf_config.write_text(
                json.dumps(
                    {
                        "watchlist_name": "ETF_FOCUS20",
                        "entries": [
                            {
                                "market": "US",
                                "symbol": "SPY",
                                "display_name": "SPDR S&P 500 ETF Trust",
                                "exchange": "NYSEARCA",
                                "instrument_type": "etf",
                                "quote_currency": "USD",
                                "timezone": "America/New_York",
                                "sort_order": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            index_config.write_text(
                json.dumps(
                    {
                        "watchlist_name": "INDEX_FOCUS20",
                        "entries": [
                            {
                                "market": "US",
                                "symbol": "SPX",
                                "display_name": "S&P 500 Index",
                                "exchange": "CBOE",
                                "instrument_type": "index",
                                "quote_currency": "USD",
                                "timezone": "America/New_York",
                                "sort_order": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            init_database(db_path)
            calls = []
            sleeps = []

            def fetcher(instrument):
                calls.append((instrument.instrument_type, instrument.symbol))
                return FakeFrame(
                    [
                        {
                            "date": "2026-04-23",
                            "open": "100",
                            "high": "105",
                            "low": "99",
                            "close": "104",
                            "volume": "1000",
                            "amount": "104000",
                        },
                        {
                            "date": "2026-04-24",
                            "open": "104",
                            "high": "108",
                            "low": "103",
                            "close": "106",
                            "volume": "1500",
                            "amount": "159000",
                        },
                    ]
                )

            with connect(db_path) as connection:
                sync_watchlist_from_file(connection, etf_config)
                sync_watchlist_from_file(connection, index_config)
                collector = AkshareCollector(
                    daily_fetcher=fetcher,
                    min_request_interval_seconds=AKSHARE_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
                    sleep=sleeps.append,
                )
                result = collector.sync_focus(
                    connection,
                    watchlist_names=["ETF_FOCUS20", "INDEX_FOCUS20"],
                    days=2,
                    snapshot_ts_utc="2026-04-24T21:00:00Z",
                    trade_date_local="2026-04-24",
                )
                daily_count = connection.execute("SELECT count(*) FROM bar_daily").fetchone()[0]
                snapshots = connection.execute(
                    """
                    SELECT instrument.symbol, market_snapshot.last_price, market_snapshot.change_pct
                    FROM market_snapshot
                    JOIN instrument
                        ON instrument.instrument_id = market_snapshot.instrument_id
                    ORDER BY instrument.symbol
                    """
                ).fetchall()

        self.assertEqual(result.source_name, "akshare")
        self.assertEqual(result.items_synced, 4)
        self.assertEqual(result.metadata["watchlists"], ["ETF_FOCUS20", "INDEX_FOCUS20"])
        self.assertEqual(calls, [("etf", "SPY"), ("index", "SPX")])
        self.assertEqual(len(sleeps), 1)
        self.assertEqual(daily_count, 4)
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0]["last_price"], 106.0)
        self.assertAlmostEqual(snapshots[0]["change_pct"], ((106.0 - 104.0) / 104.0) * 100)

    def test_akshare_collector_skips_failed_symbol_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            config_path = Path(tmp_dir) / "stocks.json"
            config_path.write_text(
                json.dumps(
                    {
                        "watchlist_name": "US_STOCK_FOCUS20",
                        "entries": [
                            {
                                "market": "US",
                                "symbol": "AAPL",
                                "display_name": "Apple",
                                "exchange": "NASDAQ",
                                "instrument_type": "stock",
                                "quote_currency": "USD",
                                "timezone": "America/New_York",
                                "sort_order": 1,
                            },
                            {
                                "market": "US",
                                "symbol": "MSFT",
                                "display_name": "Microsoft",
                                "exchange": "NASDAQ",
                                "instrument_type": "stock",
                                "quote_currency": "USD",
                                "timezone": "America/New_York",
                                "sort_order": 2,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            init_database(db_path)

            def fetcher(instrument):
                if instrument.symbol == "AAPL":
                    raise TimeoutError("upstream timeout")
                return FakeFrame(
                    [
                        {
                            "date": "2026-05-01",
                            "open": "100",
                            "high": "105",
                            "low": "99",
                            "close": "104",
                            "volume": "1000",
                            "amount": "104000",
                        }
                    ]
                )

            with connect(db_path) as connection:
                sync_watchlist_from_file(connection, config_path)
                result = AkshareCollector(
                    daily_fetcher=fetcher,
                    min_request_interval_seconds=0,
                ).sync_focus(
                    connection,
                    watchlist_names=["US_STOCK_FOCUS20"],
                    days=2,
                    snapshot_ts_utc="2026-05-01T21:00:00Z",
                    trade_date_local=None,
                )
                snapshot_count = connection.execute(
                    "SELECT count(*) FROM market_snapshot"
                ).fetchone()[0]

        self.assertEqual(result.items_synced, 1)
        self.assertEqual(result.metadata["failed_symbols"], ["US:AAPL"])
        self.assertEqual(snapshot_count, 1)

    def test_akshare_collector_times_out_slow_symbol_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            config_path = Path(tmp_dir) / "stocks.json"
            config_path.write_text(
                json.dumps(
                    {
                        "watchlist_name": "US_STOCK_FOCUS20",
                        "entries": [
                            {
                                "market": "US",
                                "symbol": "AAPL",
                                "display_name": "Apple",
                                "exchange": "NASDAQ",
                                "instrument_type": "stock",
                                "quote_currency": "USD",
                                "timezone": "America/New_York",
                                "sort_order": 1,
                            },
                            {
                                "market": "US",
                                "symbol": "MSFT",
                                "display_name": "Microsoft",
                                "exchange": "NASDAQ",
                                "instrument_type": "stock",
                                "quote_currency": "USD",
                                "timezone": "America/New_York",
                                "sort_order": 2,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            init_database(db_path)

            def fetcher(instrument):
                if instrument.symbol == "AAPL":
                    time.sleep(1)
                return FakeFrame(
                    [
                        {
                            "date": "2026-05-01",
                            "open": "100",
                            "high": "105",
                            "low": "99",
                            "close": "104",
                            "volume": "1000",
                            "amount": "104000",
                        }
                    ]
                )

            with connect(db_path) as connection:
                sync_watchlist_from_file(connection, config_path)
                result = AkshareCollector(
                    daily_fetcher=fetcher,
                    min_request_interval_seconds=0,
                    request_timeout_seconds=0.01,
                ).sync_focus(
                    connection,
                    watchlist_names=["US_STOCK_FOCUS20"],
                    days=2,
                    snapshot_ts_utc="2026-05-01T21:00:00Z",
                    trade_date_local=None,
                )
                snapshot_count = connection.execute(
                    "SELECT count(*) FROM market_snapshot"
                ).fetchone()[0]

        self.assertEqual(result.items_synced, 1)
        self.assertEqual(result.metadata["failed_symbols"], ["US:AAPL"])
        self.assertEqual(result.metadata["request_timeout_seconds"], 0.01)
        self.assertEqual(snapshot_count, 1)


if __name__ == "__main__":
    unittest.main()
