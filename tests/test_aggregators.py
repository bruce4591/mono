from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.aggregators import aggregate_daily_from_intraday, aggregate_intraday_from_1m
from market.binance import binance_symbol_to_instrument
from market.db import connect, init_database
from market.models import IntradayBar
from market.repositories import DailyBarRepository, InstrumentRepository, IntradayBarRepository


class AggregatorTests(unittest.TestCase):
    def test_aggregate_intraday_from_1m_builds_5m_and_15m_bars(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = _seed_btc_1m_bars(connection, minutes=15)
                result = aggregate_intraday_from_1m(
                    connection,
                    instrument_ids=[instrument_id],
                    target_intervals=["5m", "15m"],
                )
                bars_5m = IntradayBarRepository(connection).list_for_instrument(
                    instrument_id,
                    "5m",
                )
                bars_15m = IntradayBarRepository(connection).list_for_instrument(
                    instrument_id,
                    "15m",
                )

        self.assertEqual(result.bars_written, 4)
        self.assertEqual(len(bars_5m), 3)
        self.assertEqual(len(bars_15m), 1)
        self.assertEqual(bars_5m[0].bar_start_ts_utc, "2026-04-12T13:15:00Z")
        self.assertEqual(bars_5m[0].open, 100.0)
        self.assertEqual(bars_5m[0].high, 105.5)
        self.assertEqual(bars_5m[0].low, 99.5)
        self.assertEqual(bars_5m[0].close, 105.0)
        self.assertEqual(bars_5m[0].volume_raw, 15.0)
        self.assertEqual(bars_5m[0].turnover_raw, 1500.0)
        self.assertTrue(bars_5m[0].is_closed_bar)
        self.assertEqual(bars_15m[0].open, 100.0)
        self.assertEqual(bars_15m[0].close, 115.0)
        self.assertEqual(bars_15m[0].volume_raw, 120.0)
        self.assertEqual(bars_15m[0].turnover_raw, 12000.0)

    def test_aggregate_intraday_from_1m_marks_partial_8h_window_open(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = _seed_btc_1m_bars(connection, minutes=15)
                aggregate_intraday_from_1m(
                    connection,
                    instrument_ids=[instrument_id],
                    target_intervals=["8h"],
                )
                bars = IntradayBarRepository(connection).list_for_instrument(
                    instrument_id,
                    "8h",
                )

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].bar_start_ts_utc, "2026-04-12T08:00:00Z")
        self.assertEqual(bars[0].bar_end_ts_utc, "2026-04-12T16:00:00Z")
        self.assertFalse(bars[0].is_closed_bar)

    def test_aggregate_intraday_from_1m_only_writes_after_latest_target_bar(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = _seed_btc_1m_bars(connection, minutes=15)
                aggregate_intraday_from_1m(
                    connection,
                    instrument_ids=[instrument_id],
                    target_intervals=["5m"],
                )
                _seed_btc_1m_bars(connection, minutes=20)

                result = aggregate_intraday_from_1m(
                    connection,
                    instrument_ids=[instrument_id],
                    target_intervals=["5m"],
                )
                bars_5m = IntradayBarRepository(connection).list_for_instrument(
                    instrument_id,
                    "5m",
                )

        self.assertEqual(result.bars_written, 1)
        self.assertEqual(len(bars_5m), 4)
        self.assertEqual(bars_5m[-1].bar_start_ts_utc, "2026-04-12T13:30:00Z")

    def test_aggregate_daily_from_intraday_builds_daily_bar(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = _seed_btc_1m_bars(connection, minutes=15)
                result = aggregate_daily_from_intraday(
                    connection,
                    instrument_ids=[instrument_id],
                    source_interval="1m",
                )
                daily = DailyBarRepository(connection).list_for_instrument(instrument_id)

        self.assertEqual(result.bars_written, 1)
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0].trade_date, "2026-04-12")
        self.assertEqual(daily[0].open, 100.0)
        self.assertEqual(daily[0].close, 115.0)
        self.assertEqual(daily[0].volume_raw, 120.0)
        self.assertEqual(daily[0].turnover_raw, 12000.0)
        self.assertEqual(daily[0].source, "aggregate_1m")


def _seed_btc_1m_bars(connection, *, minutes: int) -> int:
    instrument_id = InstrumentRepository(connection).upsert(
        binance_symbol_to_instrument("BTCUSDT")
    )
    repository = IntradayBarRepository(connection)
    for index in range(minutes):
        minute = 15 + index
        repository.upsert(
            IntradayBar(
                instrument_id=instrument_id,
                interval="1m",
                bar_start_ts_utc=f"2026-04-12T13:{minute:02d}:00Z",
                bar_end_ts_utc=f"2026-04-12T13:{minute + 1:02d}:00Z",
                trade_date_local="2026-04-12",
                open=100.0 + index,
                high=101.5 + index,
                low=99.5 + index,
                close=101.0 + index,
                volume_raw=float(index + 1),
                turnover_raw=float((index + 1) * 100),
                is_closed_bar=True,
                source="binance",
            )
        )
    return instrument_id


if __name__ == "__main__":
    unittest.main()
