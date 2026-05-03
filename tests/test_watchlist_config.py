from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from market.db import connect, init_database
from market.repositories import InstrumentRepository, WatchlistRepository
from market.watchlists import sync_watchlist_from_file


class WatchlistConfigTests(unittest.TestCase):
    def test_akshare_focus_configs_cover_tradfi_tabs(self):
        expected = {
            "a_share_focus20.json": ("A_SHARE_FOCUS20", "A_SHARE", "stock"),
            "hk_stock_focus20.json": ("HK_STOCK_FOCUS20", "HK", "stock"),
            "us_stock_focus20.json": ("US_STOCK_FOCUS20", "US", "stock"),
            "etf_focus20.json": ("ETF_FOCUS20", "US", "etf"),
            "index_focus20.json": ("INDEX_FOCUS20", "US", "index"),
            "commodity_focus20.json": ("COMMODITY_FOCUS20", "CMDTY", "commodity"),
        }

        for filename, (watchlist_name, market, instrument_type) in expected.items():
            with self.subTest(filename=filename):
                payload = json.loads((Path("config/watchlists") / filename).read_text())
                entries = payload["entries"]

                self.assertEqual(payload["watchlist_name"], watchlist_name)
                self.assertGreaterEqual(len(entries), 3)
                self.assertTrue(all(entry["market"] == market for entry in entries))
                self.assertTrue(
                    all(entry["instrument_type"] == instrument_type for entry in entries)
                )

    def test_etf_focus_config_contains_ten_entries(self):
        payload = json.loads(Path("config/watchlists/etf_focus20.json").read_text())

        symbols = [entry["symbol"] for entry in payload["entries"]]

        self.assertEqual(payload["watchlist_name"], "ETF_FOCUS20")
        self.assertEqual(len(symbols), 10)
        self.assertEqual(
            symbols,
            ["SPY", "QQQ", "IWM", "DIA", "GLD", "TLT", "EEM", "XLK", "XLF", "XLE"],
        )

    def test_sync_watchlist_from_file_upserts_instruments_and_replaces_entries(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            config_path = Path(tmp_dir) / "etf_focus20.json"
            config_path.write_text(
                json.dumps(
                    {
                        "watchlist_name": "ETF_FOCUS20",
                        "entries": [
                            {
                                "market": "US",
                                "symbol": "QQQ",
                                "display_name": "Invesco QQQ",
                                "exchange": "NASDAQ",
                                "instrument_type": "etf",
                                "quote_currency": "USD",
                                "timezone": "America/New_York",
                                "sort_order": 1,
                            },
                            {
                                "market": "US",
                                "symbol": "SPY",
                                "display_name": "SPDR S&P 500 ETF",
                                "exchange": "NYSEARCA",
                                "instrument_type": "etf",
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

            with connect(db_path) as connection:
                count = sync_watchlist_from_file(connection, config_path)
                active = WatchlistRepository(connection).list_active("ETF_FOCUS20")
                qqq = InstrumentRepository(connection).get_by_market_symbol("US", "QQQ")

        self.assertEqual(count, 2)
        self.assertEqual(len(active), 2)
        self.assertIsNotNone(qqq)
        self.assertEqual(qqq.display_name, "Invesco QQQ")


if __name__ == "__main__":
    unittest.main()
