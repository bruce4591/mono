from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from market.cli import main
from market.collectors.base import CollectorResult


class CliTests(unittest.TestCase):
    def test_init_db_creates_sqlite_database(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(["init-db", "--db-path", str(db_path)])

            with sqlite3.connect(db_path) as connection:
                table_count = connection.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'instrument'"
                ).fetchone()[0]

        self.assertEqual(exit_code, 0)
        self.assertEqual(table_count, 1)

    def test_health_check_reports_ok_for_initialized_database(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                main(["init-db", "--db-path", str(db_path)])

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["health-check", "--db-path", str(db_path)])

        self.assertEqual(exit_code, 0)
        self.assertIn("database: ok", stdout.getvalue())

    def test_sync_watchlists_imports_static_config(self):
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
                                "symbol": "SPY",
                                "display_name": "SPDR S&P 500 ETF",
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
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "sync-watchlists",
                        "--db-path",
                        str(db_path),
                        "--path",
                        str(config_path),
                    ]
                )

            with sqlite3.connect(db_path) as connection:
                count = connection.execute("SELECT count(*) FROM watchlist").fetchone()[0]

        self.assertEqual(exit_code, 0)
        self.assertEqual(count, 1)
        self.assertIn("watchlist synced: ETF_FOCUS20 (1 entries)", stdout.getvalue())

    def test_refresh_rankings_accepts_empty_snapshot_set(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "refresh-rankings",
                        "--db-path",
                        str(db_path),
                        "--board-name",
                        "US_STOCK_TURNOVER_TOP50",
                        "--snapshot-ts-utc",
                        "2026-04-24T20:00:00Z",
                        "--trade-date-local",
                        "2026-04-24",
                        "--market",
                        "US",
                        "--instrument-type",
                        "stock",
                        "--limit",
                        "50",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("ranking refreshed: US_STOCK_TURNOVER_TOP50 (0 rows)", stdout.getvalue())

    def test_seed_sample_data_writes_fake_market_data(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "seed-sample-data",
                        "--db-path",
                        str(db_path),
                        "--snapshot-ts-utc",
                        "2026-04-24T20:00:00Z",
                        "--trade-date-local",
                        "2026-04-24",
                    ]
                )

            with sqlite3.connect(db_path) as connection:
                snapshot_count = connection.execute(
                    "SELECT count(*) FROM market_snapshot"
                ).fetchone()[0]
                intraday_count = connection.execute(
                    "SELECT count(*) FROM bar_intraday"
                ).fetchone()[0]

        self.assertEqual(exit_code, 0)
        self.assertEqual(snapshot_count, 5)
        self.assertEqual(intraday_count, 30)
        self.assertIn("sample data seeded: 5 instruments, 5 snapshots", stdout.getvalue())

    def test_serve_api_is_registered(self):
        parser = main(["serve-api", "--db-path", "./data/market.sqlite3", "--host", "127.0.0.1", "--port", "0", "--dry-run"])

        self.assertEqual(parser, 0)

    def test_sync_binance_klines_is_registered(self):
        exit_code = main(
            [
                "sync-binance-klines",
                "--db-path",
                "./data/market.sqlite3",
                "--symbol",
                "BTCUSDT",
                "--interval",
                "15m",
                "--limit",
                "2",
                "--dry-run",
            ]
        )

        self.assertEqual(exit_code, 0)

    def test_sync_binance_daily_is_registered(self):
        exit_code = main(
            [
                "sync-binance-daily",
                "--db-path",
                "./data/market.sqlite3",
                "--symbol",
                "BTCUSDT",
                "--days",
                "365",
                "--dry-run",
            ]
        )

        self.assertEqual(exit_code, 0)

    def test_sync_crypto_daily_defaults_to_top_quote_volume_symbols(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()
            calls = []

            class FakeBinanceCollector:
                source_name = "binance"

                def sync_daily_bars(self, connection, symbols, days):
                    calls.append((symbols, days))
                    return CollectorResult(
                        source_name=self.source_name,
                        items_synced=len(symbols) * days,
                        metadata={"symbols": symbols, "days": days},
                    )

            with redirect_stdout(stdout), patch(
                "market.cli.BinanceCollector",
                return_value=FakeBinanceCollector(),
            ), patch(
                "market.cli.fetch_top_binance_usdt_symbols",
                return_value=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            ):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "sync-crypto-daily",
                        "--db-path",
                        str(db_path),
                        "--days",
                        "365",
                        "--snapshot-ts-utc",
                        "2026-04-24T20:00:00Z",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [(["BTCUSDT", "ETHUSDT", "SOLUSDT"], 365)])
        self.assertIn("crypto daily synced: 3 symbols, 1095 daily bars", stdout.getvalue())

    def test_sync_crypto_board_is_registered(self):
        exit_code = main(
            [
                "sync-crypto-board",
                "--db-path",
                "./data/market.sqlite3",
                "--symbol",
                "BTCUSDT",
                "--symbol",
                "ETHUSDT",
                "--interval",
                "15m",
                "--limit",
                "2",
                "--dry-run",
            ]
        )

        self.assertEqual(exit_code, 0)

    def test_sync_crypto_board_defaults_to_top_quote_volume_symbols(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()
            calls = []

            class FakeBinanceCollector:
                source_name = "binance"

                def sync_intraday_bars(self, connection, symbols, interval, limit):
                    calls.append((symbols, interval, limit))
                    return CollectorResult(
                        source_name=self.source_name,
                        items_synced=len(symbols),
                        metadata={"symbols": symbols, "interval": interval},
                    )

            with redirect_stdout(stdout), patch(
                "market.cli.BinanceCollector",
                return_value=FakeBinanceCollector(),
            ), patch(
                "market.cli.fetch_top_binance_usdt_symbols",
                return_value=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            ):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "sync-crypto-board",
                        "--db-path",
                        str(db_path),
                        "--interval",
                        "15m",
                        "--limit",
                        "2",
                        "--snapshot-ts-utc",
                        "2026-04-24T20:00:00Z",
                        "--trade-date-local",
                        "2026-04-24",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [(["BTCUSDT", "ETHUSDT", "SOLUSDT"], "15m", 2)])
        self.assertIn("crypto board synced: 3 symbols", stdout.getvalue())

    def test_apply_binance_ticker_event_updates_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            event_path = Path(tmp_dir) / "ticker.json"
            event_path.write_text(
                json.dumps(
                    {
                        "e": "24hrTicker",
                        "E": 1_776_000_000_000,
                        "s": "BTCUSDT",
                        "c": "64100.00",
                        "P": "1.00",
                        "v": "10.0",
                        "q": "641000.00",
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "apply-binance-ticker-event",
                        "--db-path",
                        str(db_path),
                        "--path",
                        str(event_path),
                    ]
                )

            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    """
                    SELECT market_snapshot.last_price, market_snapshot.source
                    FROM market_snapshot
                    JOIN instrument
                        ON instrument.instrument_id = market_snapshot.instrument_id
                    WHERE instrument.symbol = 'BTCUSDT'
                    """
                ).fetchone()

        self.assertEqual(exit_code, 0)
        self.assertEqual(row, (64100.0, "binance_ws"))
        self.assertIn("binance ticker applied: BTCUSDT last_price=64100.0", stdout.getvalue())

    def test_sync_crypto_board_records_job_and_source_health(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()

            class FakeBinanceCollector:
                source_name = "binance"

                def sync_intraday_bars(self, connection, symbols, interval, limit):
                    return CollectorResult(
                        source_name=self.source_name,
                        items_synced=2,
                        metadata={"symbols": symbols, "interval": interval},
                    )

            with redirect_stdout(stdout), patch(
                "market.cli.BinanceCollector",
                return_value=FakeBinanceCollector(),
            ):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "sync-crypto-board",
                        "--db-path",
                        str(db_path),
                        "--symbol",
                        "BTCUSDT",
                        "--interval",
                        "15m",
                        "--limit",
                        "2",
                        "--snapshot-ts-utc",
                        "2026-04-24T20:00:00Z",
                        "--trade-date-local",
                        "2026-04-24",
                    ]
                )

            with sqlite3.connect(db_path) as connection:
                job = connection.execute(
                    "SELECT status, checkpoint, last_error FROM job_state WHERE job_name = ?",
                    ("sync-crypto-board",),
                ).fetchone()
                source = connection.execute(
                    "SELECT status, last_error FROM source_health WHERE source_name = ?",
                    ("binance",),
                ).fetchone()

        self.assertEqual(exit_code, 0)
        self.assertEqual(job, ("success", "BTCUSDT:15m", None))
        self.assertEqual(source, ("ok", None))

    def test_sync_crypto_board_uses_binance_collector_adapter(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()
            calls = []

            class FakeBinanceCollector:
                source_name = "binance"

                def sync_intraday_bars(self, connection, symbols, interval, limit):
                    calls.append((symbols, interval, limit))
                    return CollectorResult(
                        source_name=self.source_name,
                        items_synced=4,
                        metadata={"symbols": symbols, "interval": interval},
                    )

            with redirect_stdout(stdout), patch(
                "market.cli.BinanceCollector",
                return_value=FakeBinanceCollector(),
            ):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "sync-crypto-board",
                        "--db-path",
                        str(db_path),
                        "--symbol",
                        "BTCUSDT",
                        "--symbol",
                        "ETHUSDT",
                        "--interval",
                        "15m",
                        "--limit",
                        "2",
                        "--snapshot-ts-utc",
                        "2026-04-24T20:00:00Z",
                        "--trade-date-local",
                        "2026-04-24",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [(["BTCUSDT", "ETHUSDT"], "15m", 2)])
        self.assertIn("crypto board synced: 2 symbols, 0 ranking rows", stdout.getvalue())

    def test_sync_crypto_board_marks_job_failed_when_ranking_refresh_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()

            class FakeBinanceCollector:
                source_name = "binance"

                def sync_intraday_bars(self, connection, symbols, interval, limit):
                    return CollectorResult(
                        source_name=self.source_name,
                        items_synced=2,
                        metadata={"symbols": symbols, "interval": interval},
                    )

            def fail_refresh(self, **kwargs):
                raise RuntimeError("ranking failed")

            with redirect_stdout(stdout), patch(
                "market.cli.BinanceCollector",
                return_value=FakeBinanceCollector(),
            ), patch("market.cli.RankingRepository.refresh_turnover_board", fail_refresh):
                main(["init-db", "--db-path", str(db_path)])
                with self.assertRaises(RuntimeError):
                    main(
                        [
                            "sync-crypto-board",
                            "--db-path",
                            str(db_path),
                            "--symbol",
                            "BTCUSDT",
                            "--interval",
                            "15m",
                            "--limit",
                            "2",
                            "--snapshot-ts-utc",
                            "2026-04-24T20:00:00Z",
                            "--trade-date-local",
                            "2026-04-24",
                        ]
                    )

            with sqlite3.connect(db_path) as connection:
                job = connection.execute(
                    "SELECT status, last_error FROM job_state WHERE job_name = ?",
                    ("sync-crypto-board",),
                ).fetchone()

        self.assertEqual(tuple(job), ("failed", "ranking failed"))

    def test_add_alert_rule_and_list_alert_events_are_registered(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                main(["init-db", "--db-path", str(db_path)])
                add_exit = main(
                    [
                        "add-alert-rule",
                        "--db-path",
                        str(db_path),
                        "--name",
                        "btc change",
                        "--market",
                        "CRYPTO",
                        "--symbol",
                        "BTCUSDT",
                        "--metric",
                        "change_pct",
                        "--operator",
                        ">=",
                        "--threshold",
                        "2",
                    ]
                )
                list_exit = main(
                    [
                        "list-alert-events",
                        "--db-path",
                        str(db_path),
                        "--limit",
                        "5",
                    ]
                )

            with sqlite3.connect(db_path) as connection:
                count = connection.execute("SELECT count(*) FROM alert_rule").fetchone()[0]

        self.assertEqual(add_exit, 0)
        self.assertEqual(list_exit, 0)
        self.assertEqual(count, 1)
        self.assertIn("alert rule saved: btc change", stdout.getvalue())
        self.assertIn("[]", stdout.getvalue())

    def test_run_alerts_command_creates_event(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                main(["init-db", "--db-path", str(db_path)])
                main(
                    [
                        "seed-sample-data",
                        "--db-path",
                        str(db_path),
                        "--snapshot-ts-utc",
                        "2026-04-24T20:00:00Z",
                        "--trade-date-local",
                        "2026-04-24",
                    ]
                )
                main(
                    [
                        "add-alert-rule",
                        "--db-path",
                        str(db_path),
                        "--name",
                        "btc turnover",
                        "--market",
                        "CRYPTO",
                        "--symbol",
                        "BTCUSDT",
                        "--metric",
                        "turnover_raw",
                        "--operator",
                        ">=",
                        "--threshold",
                        "1",
                    ]
                )
                exit_code = main(
                    [
                        "run-alerts",
                        "--db-path",
                        str(db_path),
                        "--triggered-at-utc",
                        "2026-04-24T20:01:00Z",
                    ]
                )

            with sqlite3.connect(db_path) as connection:
                count = connection.execute("SELECT count(*) FROM alert_event").fetchone()[0]

        self.assertEqual(exit_code, 0)
        self.assertEqual(count, 1)
        self.assertIn("alerts evaluated: 1 rules, 1 events", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
