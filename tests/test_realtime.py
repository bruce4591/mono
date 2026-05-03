from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.binance import binance_symbol_to_instrument
from market.db import connect, init_database
from market.models import MarketSnapshot
from market import realtime
from market.realtime import (
    apply_binance_kline_event,
    apply_binance_ticker_event,
    parse_binance_kline_event,
    parse_binance_ticker_event,
)
from market.repositories import InstrumentRepository, MarketSnapshotRepository


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

    def test_parse_binance_kline_event_uses_one_minute_kline_fields(self):
        bar = parse_binance_kline_event(
            {
                "stream": "btcusdt@kline_1m",
                "data": {
                    "e": "kline",
                    "E": 1_776_000_030_000,
                    "s": "BTCUSDT",
                    "k": {
                        "t": 1_776_000_000_000,
                        "T": 1_776_000_059_999,
                        "s": "BTCUSDT",
                        "i": "1m",
                        "o": "64000.00",
                        "c": "64100.00",
                        "h": "64150.00",
                        "l": "63990.00",
                        "v": "2.5",
                        "q": "160250.00",
                        "x": False,
                    },
                },
            },
            instrument_id=7,
            timezone_name="UTC",
        )

        self.assertEqual(bar.instrument_id, 7)
        self.assertEqual(bar.interval, "1m")
        self.assertEqual(bar.bar_start_ts_utc, "2026-04-12T13:20:00Z")
        self.assertEqual(bar.bar_end_ts_utc, "2026-04-12T13:21:00Z")
        self.assertEqual(bar.trade_date_local, "2026-04-12")
        self.assertEqual(bar.open, 64000.0)
        self.assertEqual(bar.high, 64150.0)
        self.assertEqual(bar.low, 63990.0)
        self.assertEqual(bar.close, 64100.0)
        self.assertEqual(bar.volume_raw, 2.5)
        self.assertEqual(bar.turnover_raw, 160250.0)
        self.assertFalse(bar.is_closed_bar)
        self.assertEqual(bar.source, "binance_ws_kline")

    def test_apply_binance_kline_event_preserves_existing_24h_snapshot_metrics(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument = binance_symbol_to_instrument("BTCUSDT")
                instrument_id = InstrumentRepository(connection).upsert(instrument)
                MarketSnapshotRepository(connection).upsert(
                    MarketSnapshot(
                        instrument_id=instrument_id,
                        snapshot_ts_utc="2026-04-12T13:19:00Z",
                        trade_date_local="2026-04-12",
                        last_price=64000.0,
                        change_pct=1.23,
                        volume_raw=12345.0,
                        turnover_raw=987654321.0,
                        quote_currency="USDT",
                        source="binance_24hr",
                    )
                )
                bar = apply_binance_kline_event(
                    connection,
                    {
                        "e": "kline",
                        "E": 1_776_000_030_000,
                        "s": "BTCUSDT",
                        "k": {
                            "t": 1_776_000_000_000,
                            "T": 1_776_000_059_999,
                            "s": "BTCUSDT",
                            "i": "1m",
                            "o": "64000.00",
                            "c": "64100.00",
                            "h": "64150.00",
                            "l": "63990.00",
                            "v": "2.5",
                            "q": "160250.00",
                            "x": False,
                        },
                    },
                )
                snapshot = connection.execute(
                    """
                    SELECT last_price, change_pct, volume_raw, turnover_raw, source
                    FROM market_snapshot
                    WHERE instrument_id = ?
                    """,
                    (instrument_id,),
                ).fetchone()
                intraday_count = connection.execute(
                    "SELECT count(*) FROM bar_intraday WHERE interval = '1m'"
                ).fetchone()[0]

        self.assertEqual(bar.interval, "1m")
        self.assertEqual(intraday_count, 1)
        self.assertEqual(snapshot["last_price"], 64100.0)
        self.assertEqual(snapshot["change_pct"], 1.23)
        self.assertEqual(snapshot["volume_raw"], 12345.0)
        self.assertEqual(snapshot["turnover_raw"], 987654321.0)
        self.assertEqual(snapshot["source"], "binance_ws_kline_price")

    def test_apply_binance_futures_kline_event_writes_futures_bar_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                self.assertTrue(hasattr(realtime, "apply_binance_futures_kline_event"))
                bar = realtime.apply_binance_futures_kline_event(
                    connection,
                    {
                        "stream": "ethusdt@kline_1m",
                        "data": {
                            "e": "kline",
                            "E": 1_776_000_030_000,
                            "s": "ETHUSDT",
                            "k": {
                                "t": 1_776_000_000_000,
                                "T": 1_776_000_059_999,
                                "s": "ETHUSDT",
                                "i": "1m",
                                "o": "3200.00",
                                "c": "3210.00",
                                "h": "3215.00",
                                "l": "3190.00",
                                "v": "12.5",
                                "q": "40125.00",
                                "x": True,
                            },
                        },
                    },
                    aggregate=False,
                )
                row = connection.execute(
                    """
                    SELECT instrument.market,
                        instrument.instrument_type,
                        bar_intraday.close,
                        bar_intraday.source AS bar_source,
                        market_snapshot.last_price,
                        market_snapshot.source AS snapshot_source
                    FROM instrument
                    JOIN bar_intraday
                        ON bar_intraday.instrument_id = instrument.instrument_id
                    JOIN market_snapshot
                        ON market_snapshot.instrument_id = instrument.instrument_id
                    WHERE instrument.symbol = 'ETHUSDT'
                    """
                ).fetchone()

        self.assertEqual(bar.source, "binance_futures_ws_kline")
        self.assertEqual(row["market"], "CRYPTO_FUTURES")
        self.assertEqual(row["instrument_type"], "crypto_futures")
        self.assertEqual(row["close"], 3210.0)
        self.assertEqual(row["bar_source"], "binance_futures_ws_kline")
        self.assertEqual(row["last_price"], 3210.0)
        self.assertEqual(row["snapshot_source"], "binance_futures_ws_kline_price")


if __name__ == "__main__":
    unittest.main()
