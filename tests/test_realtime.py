from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.db import connect, init_database
from market.realtime import apply_binance_ticker_event, parse_binance_ticker_event


class RealtimeTests(unittest.TestCase):
    def test_parse_binance_ticker_event_uses_last_price_and_quote_volume(self):
        event = parse_binance_ticker_event(
            {
                "stream": "btcusdt@ticker",
                "data": {
                    "e": "24hrTicker",
                    "E": 1_776_000_000_000,
                    "s": "BTCUSDT",
                    "c": "64100.40",
                    "P": "1.25",
                    "v": "12.5",
                    "q": "801255.00",
                },
            }
        )

        self.assertEqual(event.symbol, "BTCUSDT")
        self.assertEqual(event.snapshot_ts_utc, "2026-04-12T13:20:00Z")
        self.assertEqual(event.trade_date_local, "2026-04-12")
        self.assertEqual(event.last_price, 64100.40)
        self.assertEqual(event.change_pct, 1.25)
        self.assertEqual(event.volume_raw, 12.5)
        self.assertEqual(event.turnover_raw, 801255.0)
        self.assertEqual(event.quote_currency, "USDT")

    def test_parse_binance_mini_ticker_event_derives_change_pct_from_open(self):
        event = parse_binance_ticker_event(
            {
                "e": "24hrMiniTicker",
                "E": 1_776_000_000_000,
                "s": "ETHUSDT",
                "o": "2000.00",
                "c": "2010.00",
                "v": "20.0",
                "q": "40100.00",
            }
        )

        self.assertEqual(event.symbol, "ETHUSDT")
        self.assertAlmostEqual(event.change_pct or 0, 0.5)
        self.assertEqual(event.turnover_raw, 40100.0)

    def test_apply_binance_ticker_event_updates_latest_snapshot_in_place(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                first = apply_binance_ticker_event(
                    connection,
                    {
                        "e": "24hrTicker",
                        "E": 1_776_000_000_000,
                        "s": "BTCUSDT",
                        "c": "64100.00",
                        "P": "1.00",
                        "v": "10.0",
                        "q": "641000.00",
                    },
                )
                second = apply_binance_ticker_event(
                    connection,
                    {
                        "e": "24hrTicker",
                        "E": 1_776_000_001_000,
                        "s": "BTCUSDT",
                        "c": "64150.00",
                        "P": "1.08",
                        "v": "10.5",
                        "q": "673575.00",
                    },
                )
                snapshot_count = connection.execute(
                    "SELECT count(*) FROM market_snapshot"
                ).fetchone()[0]
                snapshot = connection.execute(
                    """
                    SELECT last_price, change_pct, volume_raw, turnover_raw, source
                    FROM market_snapshot
                    JOIN instrument
                        ON instrument.instrument_id = market_snapshot.instrument_id
                    WHERE instrument.symbol = 'BTCUSDT'
                    """
                ).fetchone()

        self.assertEqual(first.symbol, "BTCUSDT")
        self.assertEqual(second.last_price, 64150.0)
        self.assertEqual(snapshot_count, 1)
        self.assertEqual(snapshot["last_price"], 64150.0)
        self.assertEqual(snapshot["change_pct"], 1.08)
        self.assertEqual(snapshot["volume_raw"], 10.5)
        self.assertEqual(snapshot["turnover_raw"], 673575.0)
        self.assertEqual(snapshot["source"], "binance_ws")


if __name__ == "__main__":
    unittest.main()
