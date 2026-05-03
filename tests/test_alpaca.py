from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from market.collectors.alpaca import AlpacaCollector, parse_alpaca_daily_bar
from market.db import connect, init_database
from market.repositories import DailyBarRepository
from market.watchlists import sync_watchlist_from_file


class AlpacaTests(unittest.TestCase):
    def test_parse_alpaca_daily_bar_uses_close_times_volume_turnover(self):
        bar = parse_alpaca_daily_bar(
            instrument_id=7,
            payload={
                "t": "2026-05-01T04:00:00Z",
                "o": 278.855,
                "h": 287.22,
                "l": 278.37,
                "c": 280.14,
                "v": 80105508,
            },
            quote_currency="USD",
            source="alpaca",
        )

        self.assertEqual(bar.instrument_id, 7)
        self.assertEqual(bar.trade_date, "2026-05-01")
        self.assertEqual(bar.open, 278.855)
        self.assertEqual(bar.high, 287.22)
        self.assertEqual(bar.low, 278.37)
        self.assertEqual(bar.close, 280.14)
        self.assertEqual(bar.volume_raw, 80105508.0)
        self.assertEqual(bar.turnover_raw, 280.14 * 80105508)
        self.assertEqual(bar.quote_currency, "USD")
        self.assertEqual(bar.source, "alpaca")

    def test_alpaca_collector_syncs_focus_watchlist_bars_and_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            config_path = Path(tmp_dir) / "us.json"
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
            calls = []

            def fetcher(symbols, start, end, timeout):
                calls.append((symbols, start, end, timeout))
                return {
                    "AAPL": [
                        {
                            "t": "2026-04-30T04:00:00Z",
                            "o": 270.0,
                            "h": 276.0,
                            "l": 268.0,
                            "c": 271.0,
                            "v": 1000,
                        },
                        {
                            "t": "2026-05-01T04:00:00Z",
                            "o": 278.855,
                            "h": 287.22,
                            "l": 278.37,
                            "c": 280.14,
                            "v": 80105508,
                        },
                    ],
                    "MSFT": [
                        {
                            "t": "2026-05-01T04:00:00Z",
                            "o": 500.0,
                            "h": 505.0,
                            "l": 498.0,
                            "c": 501.0,
                            "v": 2000,
                        }
                    ],
                }

            with connect(db_path) as connection:
                sync_watchlist_from_file(connection, config_path)
                result = AlpacaCollector(
                    api_key_id="paper-key",
                    api_secret_key="paper-secret",
                    bars_fetcher=fetcher,
                    request_timeout_seconds=12.5,
                ).sync_focus(
                    connection,
                    watchlist_names=["US_STOCK_FOCUS20"],
                    days=2,
                    snapshot_ts_utc="2026-05-02T01:00:00Z",
                    trade_date_local=None,
                )
                aapl = connection.execute(
                    "SELECT instrument_id FROM instrument WHERE symbol='AAPL'"
                ).fetchone()[0]
                bars = DailyBarRepository(connection).list_for_instrument(aapl)
                snapshot = connection.execute(
                    """
                    SELECT last_price, volume_raw, turnover_raw, trade_date_local, source
                    FROM market_snapshot
                    WHERE instrument_id = ?
                    """,
                    (aapl,),
                ).fetchone()

        self.assertEqual(calls[0][0], ["AAPL", "MSFT"])
        self.assertEqual(calls[0][3], 12.5)
        self.assertEqual(result.items_synced, 3)
        self.assertEqual(result.metadata["failed_symbols"], [])
        self.assertEqual(result.metadata["trade_date_local"], "2026-05-01")
        self.assertEqual(len(bars), 2)
        self.assertEqual(snapshot["last_price"], 280.14)
        self.assertEqual(snapshot["volume_raw"], 80105508.0)
        self.assertEqual(snapshot["turnover_raw"], 280.14 * 80105508)
        self.assertEqual(snapshot["trade_date_local"], "2026-05-01")
        self.assertEqual(snapshot["source"], "alpaca")

    def test_alpaca_collector_uses_non_recent_end_time_for_basic_plan(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            config_path = Path(tmp_dir) / "us.json"
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
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            init_database(db_path)
            calls = []

            def fetcher(symbols, start, end, timeout):
                calls.append((start, end))
                return {
                    "AAPL": [
                        {
                            "t": "2026-05-01T04:00:00Z",
                            "o": 278.855,
                            "h": 287.22,
                            "l": 278.37,
                            "c": 280.14,
                            "v": 80105508,
                        }
                    ]
                }

            with connect(db_path) as connection:
                sync_watchlist_from_file(connection, config_path)
                AlpacaCollector(
                    api_key_id="paper-key",
                    api_secret_key="paper-secret",
                    bars_fetcher=fetcher,
                    now_utc=lambda: datetime(2026, 5, 3, 12, 30, tzinfo=UTC),
                ).sync_focus(
                    connection,
                    watchlist_names=["US_STOCK_FOCUS20"],
                    days=365,
                    snapshot_ts_utc="2026-05-03T12:30:00Z",
                    trade_date_local=None,
                )

        self.assertEqual(calls[0][1], "2026-05-03T12:10:00Z")


if __name__ == "__main__":
    unittest.main()
