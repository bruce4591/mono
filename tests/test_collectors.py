from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError

from market.collectors.base import (
    CollectorResult,
    MarketCollector,
    run_collector_job,
)
from market.collectors.binance import (
    BINANCE_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
    BINANCE_KLINES_REQUEST_WEIGHT,
    BINANCE_SAFE_REQUEST_WEIGHT_PER_MINUTE,
    BinanceCollector,
)
from market.db import connect, init_database


class FakeCollector(MarketCollector):
    source_name = "fake"

    def sync_universe(self, connection):
        return CollectorResult(source_name=self.source_name, items_synced=1)

    def sync_daily_bars(self, connection, symbols, days):
        return CollectorResult(source_name=self.source_name, items_synced=len(symbols) * days)

    def sync_intraday_bars(self, connection, symbols, interval, limit):
        return CollectorResult(
            source_name=self.source_name,
            items_synced=len(symbols) * limit,
            metadata={"interval": interval},
        )

    def sync_snapshots(self, connection, symbols):
        return CollectorResult(source_name=self.source_name, items_synced=len(symbols))


class CollectorTests(unittest.TestCase):
    def test_binance_default_interval_is_derived_from_safe_request_weight_budget(self):
        calls_per_minute = BINANCE_SAFE_REQUEST_WEIGHT_PER_MINUTE / BINANCE_KLINES_REQUEST_WEIGHT

        self.assertEqual(BINANCE_KLINES_REQUEST_WEIGHT, 2)
        self.assertEqual(BINANCE_SAFE_REQUEST_WEIGHT_PER_MINUTE, 120)
        self.assertEqual(BINANCE_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS, 60 / calls_per_minute)

    def test_fake_collector_implements_common_contract(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                collector = FakeCollector()
                universe = collector.sync_universe(connection)
                daily = collector.sync_daily_bars(connection, ["AAA", "BBB"], days=3)
                intraday = collector.sync_intraday_bars(
                    connection,
                    ["AAA", "BBB"],
                    interval="60m",
                    limit=4,
                )
                snapshots = collector.sync_snapshots(connection, ["AAA", "BBB"])

        self.assertEqual(universe.items_synced, 1)
        self.assertEqual(daily.items_synced, 6)
        self.assertEqual(intraday.items_synced, 8)
        self.assertEqual(intraday.metadata["interval"], "60m")
        self.assertEqual(snapshots.items_synced, 2)

    def test_binance_collector_syncs_intraday_bars_with_existing_binance_logic(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            rows = [
                [
                    1_776_960_000_000,
                    "100.0",
                    "105.0",
                    "99.0",
                    "104.0",
                    "10.0",
                    1_776_960_899_999,
                    "1020.0",
                ],
                [
                    1_776_960_900_000,
                    "104.0",
                    "106.0",
                    "101.0",
                    "102.0",
                    "11.0",
                    1_776_961_799_999,
                    "1122.0",
                ],
            ]

            with connect(db_path) as connection:
                collector = BinanceCollector(
                    fetcher=lambda symbol, interval, limit: rows,
                    now_ms=1_776_961_800_000,
                )
                result = collector.sync_intraday_bars(
                    connection,
                    ["btcusdt"],
                    interval="15m",
                    limit=2,
                )
                count = connection.execute("SELECT count(*) FROM bar_intraday").fetchone()[0]
                snapshot = connection.execute(
                    """
                    SELECT last_price, change_pct, turnover_raw
                    FROM market_snapshot
                    JOIN instrument
                        ON instrument.instrument_id = market_snapshot.instrument_id
                    WHERE instrument.symbol = 'BTCUSDT'
                    """
                ).fetchone()

        self.assertEqual(result.source_name, "binance")
        self.assertEqual(result.items_synced, 2)
        self.assertEqual(result.metadata["symbols"], ["BTCUSDT"])
        self.assertEqual(result.metadata["request_weight_per_call"], 2)
        self.assertEqual(result.metadata["safe_request_weight_per_minute"], 120)
        self.assertEqual(result.metadata["min_request_interval_seconds"], 1.0)
        self.assertEqual(count, 2)
        self.assertEqual(snapshot["last_price"], 102.0)
        self.assertLess(snapshot["change_pct"], 0)
        self.assertEqual(snapshot["turnover_raw"], 1122.0)

    def test_binance_collector_syncs_daily_bars_with_existing_binance_logic(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            rows = [
                [
                    1_775_952_000_000,
                    "100.0",
                    "105.0",
                    "99.0",
                    "104.0",
                    "10.0",
                    1_776_038_399_999,
                    "1020.0",
                ],
            ]

            with connect(db_path) as connection:
                collector = BinanceCollector(fetcher=lambda symbol, interval, limit: rows)
                result = collector.sync_daily_bars(
                    connection,
                    ["btcusdt"],
                    days=365,
                )
                count = connection.execute("SELECT count(*) FROM bar_daily").fetchone()[0]

        self.assertEqual(result.source_name, "binance")
        self.assertEqual(result.items_synced, 1)
        self.assertEqual(result.metadata["symbols"], ["BTCUSDT"])
        self.assertEqual(result.metadata["days"], 365)
        self.assertEqual(result.metadata["interval"], "1d")
        self.assertEqual(count, 1)

    def test_binance_collector_rate_limits_between_symbol_requests(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []
            sleeps = []
            rows = [
                [
                    1_776_960_000_000,
                    "100.0",
                    "105.0",
                    "99.0",
                    "104.0",
                    "10.0",
                    1_776_960_899_999,
                    "1020.0",
                ]
            ]

            def fetcher(symbol, interval, limit):
                calls.append(symbol)
                return rows

            with connect(db_path) as connection:
                collector = BinanceCollector(
                    fetcher=fetcher,
                    now_ms=1_776_961_800_000,
                    min_request_interval_seconds=1.0,
                    sleep=sleeps.append,
                )
                result = collector.sync_intraday_bars(
                    connection,
                    ["BTCUSDT", "ETHUSDT"],
                    interval="15m",
                    limit=1,
                )

        self.assertEqual(result.items_synced, 2)
        self.assertEqual(calls, ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 0.9)
        self.assertLessEqual(sleeps[0], 1.0)

    def test_binance_collector_does_not_retry_failed_requests(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def fetcher(symbol, interval, limit):
                calls.append(symbol)
                raise URLError("rate limited")

            with connect(db_path) as connection:
                collector = BinanceCollector(
                    fetcher=fetcher,
                    min_request_interval_seconds=1.0,
                    sleep=lambda seconds: None,
                )
                with self.assertRaises(URLError):
                    collector.sync_intraday_bars(
                        connection,
                        ["BTCUSDT"],
                        interval="15m",
                        limit=1,
                    )

        self.assertEqual(calls, ["BTCUSDT"])

    def test_run_collector_job_records_success_health(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                result = run_collector_job(
                    connection,
                    job_name="fake-job",
                    source_name="fake",
                    checkpoint="AAA:60m",
                    started_at_utc="2026-04-24T20:00:00Z",
                    operation=lambda: CollectorResult(
                        source_name="fake",
                        items_synced=3,
                    ),
                )
                job = connection.execute(
                    "SELECT status, checkpoint, last_error FROM job_state WHERE job_name = ?",
                    ("fake-job",),
                ).fetchone()
                source = connection.execute(
                    "SELECT status, last_error FROM source_health WHERE source_name = ?",
                    ("fake",),
                ).fetchone()

        self.assertEqual(result.items_synced, 3)
        self.assertEqual(tuple(job), ("success", "AAA:60m", None))
        self.assertEqual(tuple(source), ("ok", None))


if __name__ == "__main__":
    unittest.main()
