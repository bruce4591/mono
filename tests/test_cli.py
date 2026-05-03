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

    def test_sync_akshare_focus_refreshes_etf_and_index_boards(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()
            calls = []

            class FakeAkshareCollector:
                source_name = "akshare"

                def sync_focus(
                    self,
                    connection,
                    watchlist_names,
                    days,
                    snapshot_ts_utc,
                    trade_date_local,
                ):
                    calls.append((watchlist_names, days, snapshot_ts_utc, trade_date_local))
                    rows = connection.execute(
                        """
                        SELECT instrument.instrument_id, instrument.symbol, instrument.quote_currency
                        FROM watchlist
                        JOIN instrument
                            ON instrument.instrument_id = watchlist.instrument_id
                        WHERE watchlist.watchlist_name IN (
                            'A_SHARE_FOCUS20',
                            'HK_STOCK_FOCUS20',
                            'US_STOCK_FOCUS20',
                            'ETF_FOCUS20',
                            'INDEX_FOCUS20',
                            'COMMODITY_FOCUS20'
                        )
                            AND watchlist.is_active = 1
                        ORDER BY instrument.symbol
                        """
                    ).fetchall()
                    for index, row in enumerate(rows, start=1):
                        connection.execute(
                            """
                            INSERT INTO market_snapshot (
                                instrument_id,
                                snapshot_ts_utc,
                                trade_date_local,
                                last_price,
                                change_pct,
                                volume_raw,
                                turnover_raw,
                                quote_currency,
                                source
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                int(row["instrument_id"]),
                                snapshot_ts_utc,
                                trade_date_local,
                                100.0 + index,
                                1.0,
                                1000.0 + index,
                                100000.0 + index,
                                str(row["quote_currency"]),
                                "akshare",
                            ),
                        )
                    return CollectorResult(
                        source_name=self.source_name,
                        items_synced=len(rows),
                        metadata={"watchlists": watchlist_names},
                    )

            with redirect_stdout(stdout), patch(
                "market.cli.AkshareCollector",
                return_value=FakeAkshareCollector(),
            ):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "sync-akshare-focus",
                        "--db-path",
                        str(db_path),
                        "--days",
                        "2",
                        "--snapshot-ts-utc",
                        "2026-04-24T21:00:00Z",
                        "--trade-date-local",
                        "2026-04-24",
                    ]
                )

            with sqlite3.connect(db_path) as connection:
                etf_count = connection.execute(
                    "SELECT count(*) FROM ranking_snapshot WHERE board_name = 'ETF_FOCUS20'"
                ).fetchone()[0]
                index_count = connection.execute(
                    "SELECT count(*) FROM ranking_snapshot WHERE board_name = 'INDEX_FOCUS20'"
                ).fetchone()[0]
                a_share_count = connection.execute(
                    "SELECT count(*) FROM ranking_snapshot WHERE board_name = 'A_SHARE_FOCUS20'"
                ).fetchone()[0]
                hk_count = connection.execute(
                    "SELECT count(*) FROM ranking_snapshot WHERE board_name = 'HK_STOCK_FOCUS20'"
                ).fetchone()[0]
                us_count = connection.execute(
                    "SELECT count(*) FROM ranking_snapshot WHERE board_name = 'US_STOCK_FOCUS20'"
                ).fetchone()[0]
                commodity_count = connection.execute(
                    "SELECT count(*) FROM ranking_snapshot WHERE board_name = 'COMMODITY_FOCUS20'"
                ).fetchone()[0]

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            calls,
            [
                (
                    [
                        "A_SHARE_FOCUS20",
                        "HK_STOCK_FOCUS20",
                        "US_STOCK_FOCUS20",
                        "ETF_FOCUS20",
                        "INDEX_FOCUS20",
                        "COMMODITY_FOCUS20",
                    ],
                    2,
                    "2026-04-24T21:00:00Z",
                    "2026-04-24",
                )
            ],
        )
        self.assertGreater(a_share_count, 0)
        self.assertGreater(hk_count, 0)
        self.assertGreater(us_count, 0)
        self.assertGreater(etf_count, 0)
        self.assertGreater(index_count, 0)
        self.assertGreater(commodity_count, 0)
        self.assertIn("akshare focus synced:", stdout.getvalue())
        self.assertIn("A_SHARE_FOCUS20=", stdout.getvalue())
        self.assertIn("COMMODITY_FOCUS20=", stdout.getvalue())

    def test_sync_akshare_focus_ranks_each_board_by_its_latest_trade_date(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            calls = []

            class FakeAkshareCollector:
                source_name = "akshare"

                def sync_focus(
                    self,
                    connection,
                    watchlist_names,
                    days,
                    snapshot_ts_utc,
                    trade_date_local,
                ):
                    calls.append((watchlist_names, days, snapshot_ts_utc, trade_date_local))
                    rows = connection.execute(
                        """
                        SELECT instrument.instrument_id, instrument.market, instrument.quote_currency
                        FROM watchlist
                        JOIN instrument
                            ON instrument.instrument_id = watchlist.instrument_id
                        WHERE watchlist.watchlist_name IN ('A_SHARE_FOCUS20', 'US_STOCK_FOCUS20')
                            AND watchlist.is_active = 1
                        ORDER BY instrument.market
                        """
                    ).fetchall()
                    for row in rows:
                        resolved_trade_date = (
                            "2026-04-30" if row["market"] == "A_SHARE" else "2026-05-01"
                        )
                        connection.execute(
                            """
                            INSERT INTO market_snapshot (
                                instrument_id,
                                snapshot_ts_utc,
                                trade_date_local,
                                last_price,
                                change_pct,
                                volume_raw,
                                turnover_raw,
                                quote_currency,
                                source
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                int(row["instrument_id"]),
                                snapshot_ts_utc,
                                resolved_trade_date,
                                100.0,
                                1.0,
                                1000.0,
                                100000.0,
                                str(row["quote_currency"]),
                                "akshare",
                            ),
                        )
                    return CollectorResult(
                        source_name=self.source_name,
                        items_synced=len(rows),
                        metadata={"trade_date_local": "2026-05-01"},
                    )

            with redirect_stdout(io.StringIO()), patch(
                "market.cli.AkshareCollector",
                return_value=FakeAkshareCollector(),
            ):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "sync-akshare-focus",
                        "--db-path",
                        str(db_path),
                        "--watchlist-config",
                        "config/watchlists/a_share_focus20.json",
                        "--watchlist-config",
                        "config/watchlists/us_stock_focus20.json",
                        "--snapshot-ts-utc",
                        "2026-05-01T21:00:00Z",
                    ]
                )

            with sqlite3.connect(db_path) as connection:
                a_share_count = connection.execute(
                    "SELECT count(*) FROM ranking_snapshot WHERE board_name = 'A_SHARE_FOCUS20'"
                ).fetchone()[0]
                us_count = connection.execute(
                    "SELECT count(*) FROM ranking_snapshot WHERE board_name = 'US_STOCK_FOCUS20'"
                ).fetchone()[0]
                hk_count = connection.execute(
                    "SELECT count(*) FROM ranking_snapshot WHERE board_name = 'HK_STOCK_FOCUS20'"
                ).fetchone()[0]

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            calls,
            [
                (
                    ["A_SHARE_FOCUS20", "US_STOCK_FOCUS20"],
                    365,
                    "2026-05-01T21:00:00Z",
                    None,
                )
            ],
        )
        self.assertGreater(a_share_count, 0)
        self.assertGreater(us_count, 0)
        self.assertEqual(hk_count, 0)

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

    def test_run_binance_kline_ws_is_registered(self):
        exit_code = main(
            [
                "run-binance-kline-ws",
                "--db-path",
                "./data/market.sqlite3",
                "--symbol",
                "BTCUSDT",
                "--interval",
                "1m",
                "--dry-run",
            ]
        )

        self.assertEqual(exit_code, 0)

    def test_run_binance_futures_kline_ws_is_registered(self):
        exit_code = main(
            [
                "run-binance-futures-kline-ws",
                "--db-path",
                "./data/market.sqlite3",
                "--symbol",
                "BTCUSDT",
                "--interval",
                "1m",
                "--dry-run",
            ]
        )

        self.assertEqual(exit_code, 0)

    def test_run_binance_futures_kline_ws_defaults_to_top_futures_symbols(self):
        stdout = io.StringIO()
        tickers = [
            {"symbol": "ETHUSDT", "quoteVolume": "2000"},
            {"symbol": "BTCUSDT", "quoteVolume": "1000"},
        ]
        exchange_info = {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "quoteAsset": "USDT",
            },
            "ETHUSDT": {
                "symbol": "ETHUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "quoteAsset": "USDT",
            },
        }

        with redirect_stdout(stdout), patch(
            "market.cli.fetch_binance_futures_24hr_tickers",
            return_value=tickers,
        ), patch(
            "market.cli.fetch_binance_futures_exchange_info",
            return_value=exchange_info,
        ):
            exit_code = main(
                [
                    "run-binance-futures-kline-ws",
                    "--db-path",
                    "./data/market.sqlite3",
                    "--top-usdt-limit",
                    "2",
                    "--interval",
                    "1m",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("ETHUSDT,BTCUSDT interval=1m", stdout.getvalue())

    def test_run_binance_futures_kline_ws_includes_tradefi_symbols_by_default(self):
        stdout = io.StringIO()
        tickers = [
            {"symbol": "BTCUSDT", "quoteVolume": "2000"},
            {"symbol": "COINUSDT", "quoteVolume": "100"},
        ]
        exchange_info = {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "quoteAsset": "USDT",
                "underlyingSubType": ["PoW"],
            },
            "COINUSDT": {
                "symbol": "COINUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "quoteAsset": "USDT",
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
            exit_code = main(
                [
                    "run-binance-futures-kline-ws",
                    "--db-path",
                    "./data/market.sqlite3",
                    "--top-usdt-limit",
                    "1",
                    "--interval",
                    "1m",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("BTCUSDT,COINUSDT interval=1m", stdout.getvalue())

    def test_run_binance_futures_kline_ws_default_limit_is_sixty(self):
        observed_limits = []

        with redirect_stdout(io.StringIO()), patch(
            "market.cli.fetch_binance_futures_24hr_tickers",
            return_value=[],
        ), patch(
            "market.cli.fetch_binance_futures_exchange_info",
            return_value={},
        ), patch(
            "market.cli.select_top_futures_usdt_symbols",
            side_effect=lambda tickers, exchange_info, *, limit: observed_limits.append(limit)
            or [],
        ):
            exit_code = main(
                [
                    "run-binance-futures-kline-ws",
                    "--db-path",
                    "./data/market.sqlite3",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(observed_limits, [60])

    def test_run_binance_futures_kline_ws_uses_market_websocket_route(self):
        created = []

        class FakeCollector:
            def __init__(self, **kwargs):
                created.append(kwargs)

            def run_forever(self):
                return CollectorResult(
                    source_name="binance_futures_ws_kline",
                    items_synced=0,
                    metadata={},
                )

        with patch("market.cli.BinanceKlineWebSocketCollector", FakeCollector):
            exit_code = main(
                [
                    "run-binance-futures-kline-ws",
                    "--db-path",
                    "./data/market.sqlite3",
                    "--symbol",
                    "ETHUSDT",
                    "--interval",
                    "1m",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(created[0]["ws_base_url"], "wss://fstream.binance.com/market")

    def test_run_binance_kline_ws_can_use_top_usdt_symbols(self):
        stdout = io.StringIO()

        with redirect_stdout(stdout), patch(
            "market.cli.fetch_top_binance_usdt_symbols",
            return_value=["BTCUSDT", "ETHUSDT"],
        ):
            exit_code = main(
                [
                    "run-binance-kline-ws",
                    "--db-path",
                    "./data/market.sqlite3",
                    "--top-usdt-limit",
                    "2",
                    "--interval",
                    "1m",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("BTCUSDT,ETHUSDT interval=1m", stdout.getvalue())

    def test_run_binance_kline_ws_does_not_gap_fill_in_realtime_path_by_default(self):
        created = []

        class FakeCollector:
            def __init__(self, **kwargs):
                created.append(kwargs)

            def run_forever(self):
                return CollectorResult(
                    source_name="binance_ws_kline",
                    items_synced=0,
                    metadata={},
                )

        with patch("market.cli.BinanceKlineWebSocketCollector", FakeCollector):
            exit_code = main(
                [
                    "run-binance-kline-ws",
                    "--db-path",
                    "./data/market.sqlite3",
                    "--symbol",
                    "BTCUSDT",
                    "--interval",
                    "1m",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(created[0]["symbols"], ["BTCUSDT"])
        self.assertFalse(created[0]["gap_fill_on_reconnect"])

    def test_fill_crypto_kline_gaps_uses_top_symbols_and_lookback_window(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()
            calls = []

            class FakeGapResult:
                symbols_checked = 2
                gaps_filled = 3
                bars_written = 3
                aggregate_bars_written = 6

            def fill_gaps(connection, **kwargs):
                calls.append(kwargs)
                return FakeGapResult()

            with redirect_stdout(stdout), patch(
                "market.cli.fetch_top_binance_usdt_symbols",
                return_value=["BTCUSDT", "ETHUSDT"],
            ), patch(
                "market.cli.fill_binance_1m_gaps",
                side_effect=fill_gaps,
            ):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "fill-crypto-kline-gaps",
                        "--db-path",
                        str(db_path),
                        "--lookback-minutes",
                        "3",
                        "--end-ts-utc",
                        "2026-04-24T00:03:30Z",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["symbols"], ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(calls[0]["start_ts_utc"], "2026-04-24T00:00:30Z")
        self.assertEqual(calls[0]["end_ts_utc"], "2026-04-24T00:03:30Z")
        self.assertIn(
            "crypto gaps filled: 2 symbols, 3 missing minutes, 3 bars",
            stdout.getvalue(),
        )

    def test_sync_crypto_board_defaults_to_top_quote_volume_symbols(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()
            calls = []
            tickers = [
                {
                    "symbol": "SOLUSDT",
                    "quoteVolume": "3000",
                    "lastPrice": "150",
                    "priceChangePercent": "3",
                    "volume": "20",
                },
                {
                    "symbol": "BTCUSDT",
                    "quoteVolume": "2000",
                    "lastPrice": "78000",
                    "priceChangePercent": "2",
                    "volume": "0.1",
                },
                {
                    "symbol": "ETHUSDT",
                    "quoteVolume": "1000",
                    "lastPrice": "3000",
                    "priceChangePercent": "1",
                    "volume": "0.3",
                },
            ]

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
                "market.cli.fetch_binance_24hr_tickers",
                return_value=tickers,
            ):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "sync-crypto-board",
                        "--db-path",
                        str(db_path),
                        "--limit",
                        "2",
                        "--snapshot-ts-utc",
                        "2026-04-24T20:00:00Z",
                        "--trade-date-local",
                        "2026-04-24",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [(["SOLUSDT", "BTCUSDT", "ETHUSDT"], "1m", 2)])
        self.assertIn("crypto board synced: 3 symbols", stdout.getvalue())

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
            ), patch(
                "market.cli.fetch_binance_24hr_tickers",
                return_value=[
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "78012.00",
                        "priceChangePercent": "-1.25",
                        "volume": "12345.67",
                        "quoteVolume": "987654321.12",
                    },
                    {
                        "symbol": "ETHUSDT",
                        "lastPrice": "3000.00",
                        "priceChangePercent": "2.50",
                        "volume": "20000",
                        "quoteVolume": "60000000",
                    },
                ],
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
            aggregate_calls = []

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
            ), patch(
                "market.cli.fetch_binance_24hr_tickers",
                return_value=[
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "78012.00",
                        "priceChangePercent": "-1.25",
                        "volume": "12345.67",
                        "quoteVolume": "987654321.12",
                    },
                    {
                        "symbol": "ETHUSDT",
                        "lastPrice": "3000.00",
                        "priceChangePercent": "2.50",
                        "volume": "20000",
                        "quoteVolume": "60000000",
                    },
                ],
            ), patch(
                "market.cli.aggregate_crypto_from_1m",
                side_effect=lambda connection, symbols: aggregate_calls.append(symbols),
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
                        "--limit",
                        "2",
                        "--snapshot-ts-utc",
                        "2026-04-24T20:00:00Z",
                        "--trade-date-local",
                        "2026-04-24",
                    ]
                )
                with sqlite3.connect(db_path) as connection:
                    rows = connection.execute(
                        """
                        SELECT instrument.symbol,
                            market_snapshot.last_price,
                            market_snapshot.change_pct,
                            market_snapshot.volume_raw,
                            market_snapshot.turnover_raw,
                            market_snapshot.source
                        FROM market_snapshot
                        JOIN instrument
                            ON instrument.instrument_id = market_snapshot.instrument_id
                        ORDER BY market_snapshot.turnover_raw DESC
                        """
                    ).fetchall()

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [(["BTCUSDT", "ETHUSDT"], "1m", 2)])
        self.assertEqual(aggregate_calls, [["BTCUSDT", "ETHUSDT"]])
        self.assertEqual(
            [tuple(row) for row in rows],
            [
                ("BTCUSDT", 78012.0, -1.25, 12345.67, 987654321.12, "binance_24hr"),
                ("ETHUSDT", 3000.0, 2.5, 20000.0, 60000000.0, "binance_24hr"),
            ],
        )
        self.assertIn("crypto board synced: 2 symbols, 2 ranking rows", stdout.getvalue())

    def test_sync_crypto_board_can_skip_rest_kline_sync_without_aggregating(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()
            aggregate_calls = []

            class FakeBinanceCollector:
                source_name = "binance"

                def sync_intraday_bars(self, connection, symbols, interval, limit):
                    raise AssertionError("REST kline sync should be skipped")

            with redirect_stdout(stdout), patch(
                "market.cli.BinanceCollector",
                return_value=FakeBinanceCollector(),
            ), patch(
                "market.cli.fetch_binance_24hr_tickers",
                return_value=[
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "78012.00",
                        "priceChangePercent": "-1.25",
                        "volume": "12345.67",
                        "quoteVolume": "987654321.12",
                    }
                ],
            ), patch(
                "market.cli.aggregate_crypto_from_1m",
                side_effect=lambda connection, symbols: aggregate_calls.append(symbols),
            ):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "sync-crypto-board",
                        "--db-path",
                        str(db_path),
                        "--symbol",
                        "BTCUSDT",
                        "--skip-kline-sync",
                        "--snapshot-ts-utc",
                        "2026-04-24T20:00:00Z",
                        "--trade-date-local",
                        "2026-04-24",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(aggregate_calls, [])
        self.assertIn("crypto board synced: 1 symbols, 1 ranking rows", stdout.getvalue())

    def test_aggregate_crypto_klines_aggregates_local_one_minute_bars(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()
            aggregate_calls = []

            def aggregate(connection, symbols):
                aggregate_calls.append(symbols)
                return type("Result", (), {"bars_written": 3})()

            with redirect_stdout(stdout), patch(
                "market.cli.aggregate_crypto_from_1m",
                side_effect=aggregate,
            ):
                main(["init-db", "--db-path", str(db_path)])
                exit_code = main(
                    [
                        "aggregate-crypto-klines",
                        "--db-path",
                        str(db_path),
                        "--symbol",
                        "btcusdt",
                        "--symbol",
                        "ETHUSDT",
                        "--started-at-utc",
                        "2026-04-24T20:00:00Z",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(aggregate_calls, [["BTCUSDT", "ETHUSDT"]])
        self.assertIn("crypto klines aggregated: 2 symbols", stdout.getvalue())

    def test_aggregate_crypto_klines_defaults_to_local_snapshot_turnover_order(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            stdout = io.StringIO()
            aggregate_calls = []

            def aggregate(connection, symbols):
                aggregate_calls.append(symbols)
                return type("Result", (), {"bars_written": 0})()

            with redirect_stdout(stdout), patch(
                "market.cli.aggregate_crypto_from_1m",
                side_effect=aggregate,
            ):
                main(["init-db", "--db-path", str(db_path)])
                with sqlite3.connect(db_path) as connection:
                    connection.execute(
                        """
                        INSERT INTO instrument (
                            instrument_id, market, symbol, display_name, exchange,
                            instrument_type, quote_currency, timezone, extra_meta
                        )
                        VALUES
                            (1, 'CRYPTO', 'BTCUSDT', 'BTCUSDT', 'BINANCE', 'crypto', 'USDT', 'UTC', '{}'),
                            (2, 'CRYPTO', 'ETHUSDT', 'ETHUSDT', 'BINANCE', 'crypto', 'USDT', 'UTC', '{}')
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO market_snapshot (
                            instrument_id, snapshot_ts_utc, trade_date_local,
                            last_price, turnover_raw, quote_currency, source
                        )
                        VALUES
                            (1, '2026-04-24T20:00:00Z', '2026-04-24', 1, 10, 'USDT', 'binance_24hr'),
                            (2, '2026-04-24T20:00:00Z', '2026-04-24', 1, 20, 'USDT', 'binance_24hr')
                        """
                    )
                    connection.commit()
                exit_code = main(
                    [
                        "aggregate-crypto-klines",
                        "--db-path",
                        str(db_path),
                        "--top-usdt-limit",
                        "2",
                        "--started-at-utc",
                        "2026-04-24T20:00:00Z",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(aggregate_calls, [["ETHUSDT", "BTCUSDT"]])

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

    def test_aggregate_crypto_futures_klines_defaults_to_top_futures_symbols(self):
        stdout = io.StringIO()
        tickers = [
            {"symbol": "ETHUSDT", "quoteVolume": "2000"},
            {"symbol": "BTCUSDT", "quoteVolume": "1000"},
        ]
        exchange_info = {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "quoteAsset": "USDT",
            },
            "ETHUSDT": {
                "symbol": "ETHUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "quoteAsset": "USDT",
            },
        }

        with redirect_stdout(stdout), patch(
            "market.cli.fetch_binance_futures_24hr_tickers",
            return_value=tickers,
        ), patch(
            "market.cli.fetch_binance_futures_exchange_info",
            return_value=exchange_info,
        ):
            exit_code = main(
                [
                    "aggregate-crypto-futures-klines",
                    "--db-path",
                    "./data/market.sqlite3",
                    "--top-usdt-limit",
                    "2",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("crypto futures aggregate ready: ETHUSDT,BTCUSDT", stdout.getvalue())

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
            ), patch(
                "market.cli.fetch_binance_24hr_tickers",
                return_value=[
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "78012.00",
                        "priceChangePercent": "-1.25",
                        "volume": "12345.67",
                        "quoteVolume": "987654321.12",
                    }
                ],
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
