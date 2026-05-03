from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from market.api import (
    get_alert_events_payload,
    get_alert_metrics_payload,
    get_alert_rules_payload,
    get_daily_bars_payload,
    get_health_payload,
    get_instrument_payload,
    get_intraday_bars_payload,
    get_jobs_payload,
    get_board_payload,
    get_static_asset,
    get_watchlists_payload,
)
from market.binance import binance_symbol_to_instrument
from market.binance_futures import binance_futures_symbol_to_instrument
from market.db import connect, init_database
from market.repositories import InstrumentRepository, IntradayBarRepository, RankingRepository
from market.models import AlertRule, IntradayBar
from market.repositories import AlertEventRepository, AlertRuleRepository
from market.models import AlertEvent
from market.sample_data import seed_sample_data
from market.watchlists import sync_watchlist_from_file


class ApiTests(unittest.TestCase):
    def test_get_board_payload_returns_ranked_instruments(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                sync_watchlist_from_file(connection, Path("config/watchlists/etf_focus20.json"))
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                RankingRepository(connection).refresh_turnover_board(
                    board_name="ETF_FOCUS20",
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                    market="US",
                    instrument_type="etf",
                    limit=20,
                    watchlist_name="ETF_FOCUS20",
                )
                payload = get_board_payload(connection, "ETF_FOCUS20")

        self.assertEqual(payload["board_name"], "ETF_FOCUS20")
        self.assertEqual(payload["snapshot_ts_utc"], "2026-04-24T20:00:00Z")
        self.assertEqual(payload["items"][0]["rank"], 1)
        self.assertEqual(payload["items"][0]["symbol"], "SPY")
        self.assertEqual(payload["items"][0]["volume_raw"], 72000000.0)
        self.assertEqual(payload["items"][0]["turnover_raw"], 36734400000.0)

    def test_get_board_payload_uses_latest_snapshot_volume_when_ranking_time_differs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                RankingRepository(connection).refresh_turnover_board(
                    board_name="CRYPTO_TURNOVER_TOP50",
                    snapshot_ts_utc="2026-04-24T20:05:00Z",
                    trade_date_local="2026-04-24",
                    market="CRYPTO",
                    instrument_type="crypto",
                    limit=50,
                )
                payload = get_board_payload(connection, "CRYPTO_TURNOVER_TOP50")

        self.assertEqual(payload["items"][0]["symbol"], "BTCUSDT")
        self.assertEqual(payload["items"][0]["volume_raw"], 42000.0)

    def test_get_board_payload_returns_latest_price_and_rank_change(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                btc = get_instrument_payload(connection, "CRYPTO", "BTCUSDT")
                eth = get_instrument_payload(connection, "CRYPTO", "ETHUSDT")
                assert btc is not None
                assert eth is not None
                get_instrument_payload(
                    connection,
                    "CRYPTO",
                    "BTCUSDT",
                    instrument_metadata_fetcher=lambda _symbol: {
                        "price_tick_size": "0.01000000",
                    },
                )
                connection.executemany(
                    """
                    INSERT INTO ranking_snapshot (
                        board_name,
                        snapshot_ts_utc,
                        rank,
                        instrument_id,
                        turnover_raw,
                        quote_currency,
                        change_pct,
                        source
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "CRYPTO_TURNOVER_TOP50",
                            "2026-04-24T19:45:00Z",
                            1,
                            eth["instrument_id"],
                            1_208_400_000.0,
                            "USDT",
                            1.76,
                            "test",
                        ),
                        (
                            "CRYPTO_TURNOVER_TOP50",
                            "2026-04-24T19:45:00Z",
                            2,
                            btc["instrument_id"],
                            2_698_500_000.0,
                            "USDT",
                            2.14,
                            "test",
                        ),
                    ],
                )
                RankingRepository(connection).refresh_turnover_board(
                    board_name="CRYPTO_TURNOVER_TOP50",
                    snapshot_ts_utc="2026-04-24T20:05:00Z",
                    trade_date_local="2026-04-24",
                    market="CRYPTO",
                    instrument_type="crypto",
                    limit=50,
                )
                payload = get_board_payload(connection, "CRYPTO_TURNOVER_TOP50")

        self.assertEqual(payload["items"][0]["symbol"], "BTCUSDT")
        self.assertEqual(payload["items"][0]["last_price"], 64250.0)
        self.assertEqual(payload["items"][0]["price_tick_size"], "0.01000000")
        self.assertEqual(payload["previous_snapshot_ts_utc"], "2026-04-24T19:45:00Z")
        self.assertEqual(payload["items"][0]["previous_rank"], 2)
        self.assertEqual(payload["items"][0]["rank_change"], 1)
        self.assertEqual(payload["items"][1]["symbol"], "ETHUSDT")
        self.assertEqual(payload["items"][1]["previous_rank"], 1)
        self.assertEqual(payload["items"][1]["rank_change"], -1)

    def test_get_board_payload_returns_empty_items_for_unknown_board(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                payload = get_board_payload(connection, "MISSING_BOARD")

        self.assertEqual(payload["board_name"], "MISSING_BOARD")
        self.assertIsNone(payload["snapshot_ts_utc"])
        self.assertIsNone(payload["previous_snapshot_ts_utc"])
        self.assertEqual(payload["items"], [])

    def test_get_instrument_payload_returns_instrument_and_latest_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                payload = get_instrument_payload(connection, "US", "SPY")

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["market"], "US")
        self.assertEqual(payload["symbol"], "SPY")
        self.assertEqual(payload["latest_snapshot"]["last_price"], 510.2)

    def test_get_instrument_payload_caches_crypto_tick_size_metadata(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def metadata_fetcher(symbol: str) -> dict[str, object]:
                calls.append(symbol)
                return {"price_tick_size": "0.00001000"}

            with connect(db_path) as connection:
                InstrumentRepository(connection).upsert(
                    binance_symbol_to_instrument("DOGEUSDT")
                )
                payload = get_instrument_payload(
                    connection,
                    "CRYPTO",
                    "DOGEUSDT",
                    instrument_metadata_fetcher=metadata_fetcher,
                )
                stored = InstrumentRepository(connection).get_by_market_symbol(
                    "CRYPTO", "DOGEUSDT"
                )

        self.assertEqual(calls, ["DOGEUSDT"])
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["extra_meta"]["price_tick_size"], "0.00001000")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.extra_meta["price_tick_size"], "0.00001000")

    def test_get_daily_bars_payload_returns_symbol_bars(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                payload = get_daily_bars_payload(connection, "US", "SPY")

        self.assertEqual(payload["market"], "US")
        self.assertEqual(payload["symbol"], "SPY")
        self.assertEqual(payload["interval"], "1d")
        self.assertEqual(len(payload["items"]), 5)
        self.assertEqual(payload["items"][0]["interval"], "1d")
        self.assertEqual(payload["items"][-1]["close"], 510.2)

    def test_get_intraday_bars_payload_returns_interval_bars(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                payload = get_intraday_bars_payload(connection, "CRYPTO", "BTCUSDT", "15m")

        self.assertEqual(payload["market"], "CRYPTO")
        self.assertEqual(payload["symbol"], "BTCUSDT")
        self.assertEqual(payload["interval"], "15m")
        self.assertEqual(len(payload["items"]), 6)
        self.assertEqual(payload["items"][0]["interval"], "15m")
        self.assertTrue(payload["items"][-1]["is_closed_bar"])

    def test_get_intraday_bars_payload_limits_latest_window(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                payload = get_intraday_bars_payload(
                    connection,
                    "CRYPTO",
                    "BTCUSDT",
                    "15m",
                    limit=2,
                )

        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["items"][0]["bar_start_ts_utc"], "2026-04-24T15:00:00Z")
        self.assertEqual(payload["items"][1]["bar_start_ts_utc"], "2026-04-24T15:15:00Z")

    def test_get_intraday_bars_payload_backfills_crypto_window_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def fetcher(symbol, interval, start_time_ms, end_time_ms, limit):
                calls.append((symbol, interval, start_time_ms, end_time_ms, limit))
                return [
                    [
                        1_776_000_000_000,
                        "64000",
                        "64100",
                        "63900",
                        "64050",
                        "1",
                        1_776_000_059_999,
                        "64050",
                    ],
                    [
                        1_776_000_060_000,
                        "64050",
                        "64200",
                        "64000",
                        "64150",
                        "2",
                        1_776_000_119_999,
                        "128300",
                    ],
                ]

            with connect(db_path) as connection:
                InstrumentRepository(connection).upsert(
                    binance_symbol_to_instrument("BTCUSDT")
                )
                payload = get_intraday_bars_payload(
                    connection,
                    "CRYPTO",
                    "BTCUSDT",
                    "1m",
                    before_ts_utc="2026-04-12T13:22:00Z",
                    limit=2,
                    gap_fetcher=fetcher,
                    gap_min_request_interval_seconds=0,
                )

        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["items"][0]["source"], "binance_gap_fill")
        self.assertEqual(payload["items"][1]["close"], 64150.0)
        self.assertEqual(calls[0][0], "BTCUSDT")
        self.assertEqual(calls[0][1], "1m")

    def test_get_intraday_bars_payload_backfills_up_to_1000_1m_bars_when_window_is_short(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def fetcher(symbol, interval, start_time_ms, end_time_ms, limit):
                calls.append((symbol, interval, start_time_ms, end_time_ms, limit))
                return []

            with connect(db_path) as connection:
                InstrumentRepository(connection).upsert(
                    binance_symbol_to_instrument("BTCUSDT")
                )
                payload = get_intraday_bars_payload(
                    connection,
                    "CRYPTO",
                    "BTCUSDT",
                    "5m",
                    before_ts_utc="2026-04-12T13:22:00Z",
                    limit=96,
                    gap_fetcher=fetcher,
                    gap_min_request_interval_seconds=0,
                )

        self.assertEqual(payload["items"], [])
        self.assertEqual(calls, [("BTCUSDT", "1m", 1_775_940_120_000, 1_776_000_119_999, 1000)])

    def test_get_intraday_bars_payload_backfills_latest_crypto_window_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def fetcher(symbol, interval, start_time_ms, end_time_ms, limit):
                calls.append((symbol, interval, start_time_ms, end_time_ms, limit))
                return [
                    [
                        1_776_000_000_000,
                        "64000",
                        "64100",
                        "63900",
                        "64050",
                        "1",
                        1_776_000_059_999,
                        "64050",
                    ],
                ]

            with connect(db_path) as connection:
                InstrumentRepository(connection).upsert(
                    binance_symbol_to_instrument("BTCUSDT")
                )
                payload = get_intraday_bars_payload(
                    connection,
                    "CRYPTO",
                    "BTCUSDT",
                    "1m",
                    limit=2,
                    now_ts_utc="2026-04-12T13:22:00Z",
                    gap_fetcher=fetcher,
                    gap_min_request_interval_seconds=0,
                )

        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["source"], "binance_gap_fill")
        self.assertEqual(calls[0], ("BTCUSDT", "1m", 1_775_940_120_000, 1_776_000_119_999, 1000))

    def test_get_intraday_bars_payload_backfills_futures_window_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def fetcher(symbol, interval, start_time_ms, end_time_ms, limit):
                calls.append((symbol, interval, limit))
                return [
                    [
                        _ms("2026-05-03T00:00:00Z"),
                        "1",
                        "2",
                        "0.5",
                        "1.5",
                        "10",
                        _ms("2026-05-03T00:00:59.999Z"),
                        "15",
                    ]
                ]

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

    def test_get_intraday_bars_payload_backfills_futures_window_without_existing_instrument(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def fetcher(symbol, interval, start_time_ms, end_time_ms, limit):
                calls.append((symbol, interval, limit))
                return [
                    [
                        _ms("2026-05-03T00:00:00Z"),
                        "1",
                        "2",
                        "0.5",
                        "1.5",
                        "10",
                        _ms("2026-05-03T00:00:59.999Z"),
                        "15",
                    ]
                ]

            with connect(db_path) as connection:
                payload = get_intraday_bars_payload(
                    connection,
                    "CRYPTO_FUTURES",
                    "ETHUSDT",
                    "1m",
                    now_ts_utc="2026-05-03T00:01:00Z",
                    gap_fetcher=fetcher,
                    gap_min_request_interval_seconds=0,
                )

        self.assertEqual(payload["items"][0]["source"], "binance_futures_gap_fill")
        self.assertEqual(payload["items"][0]["high"], 2.0)
        self.assertEqual(calls, [("ETHUSDT", "1m", 1000)])

    def test_get_intraday_bars_payload_backfills_sixty_futures_interval_bars(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def fetcher(symbol, interval, start_time_ms, end_time_ms, limit):
                calls.append((symbol, interval, limit))
                rows = []
                start_ms = _ms("2026-04-13T00:00:00Z")
                for index in range(60):
                    open_time = start_ms + index * 8 * 60 * 60 * 1000
                    rows.append(
                        [
                            open_time,
                            str(100 + index),
                            str(101 + index),
                            str(99 + index),
                            str(100.5 + index),
                            "10",
                            open_time + 8 * 60 * 60 * 1000 - 1,
                            "1000",
                        ]
                    )
                return rows

            with connect(db_path) as connection:
                instrument_id = InstrumentRepository(connection).upsert(
                    binance_futures_symbol_to_instrument({"symbol": "ETHUSDT"})
                )
                IntradayBarRepository(connection).upsert(
                    IntradayBar(
                        instrument_id=instrument_id,
                        interval="8h",
                        bar_start_ts_utc="2026-05-03T00:00:00Z",
                        bar_end_ts_utc="2026-05-03T08:00:00Z",
                        trade_date_local="2026-05-03",
                        open=1,
                        high=2,
                        low=0.5,
                        close=1.5,
                        volume_raw=10,
                        turnover_raw=15,
                        is_closed_bar=True,
                        source="aggregate_1m",
                    )
                )
                payload = get_intraday_bars_payload(
                    connection,
                    "CRYPTO_FUTURES",
                    "ETHUSDT",
                    "8h",
                    limit=60,
                    now_ts_utc="2026-05-03T00:00:00Z",
                    gap_fetcher=fetcher,
                    gap_min_request_interval_seconds=0,
                )

        self.assertEqual(payload["interval"], "8h")
        self.assertEqual(len(payload["items"]), 60)
        self.assertEqual(payload["items"][0]["source"], "binance_futures_gap_fill")
        self.assertEqual(calls, [("ETHUSDT", "8h", 60)])

    def test_get_daily_bars_payload_backfills_sixty_futures_daily_bars(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def fetcher(symbol, interval, start_time_ms, end_time_ms, limit):
                calls.append((symbol, interval, limit))
                rows = []
                start_ms = _ms("2026-03-05T00:00:00Z")
                for index in range(60):
                    open_time = start_ms + index * 24 * 60 * 60 * 1000
                    rows.append(
                        [
                            open_time,
                            str(100 + index),
                            str(101 + index),
                            str(99 + index),
                            str(100.5 + index),
                            "10",
                            open_time + 24 * 60 * 60 * 1000 - 1,
                            "1000",
                        ]
                    )
                return rows

            with connect(db_path) as connection:
                payload = get_daily_bars_payload(
                    connection,
                    "CRYPTO_FUTURES",
                    "ETHUSDT",
                    now_ts_utc="2026-05-03T00:00:00Z",
                    futures_fetcher=fetcher,
                )

        self.assertEqual(payload["interval"], "1d")
        self.assertEqual(len(payload["items"]), 60)
        self.assertEqual(payload["items"][0]["source"], "binance_futures_gap_fill")
        self.assertEqual(calls, [("ETHUSDT", "1d", 60)])

    def test_get_intraday_bars_payload_backfills_stale_latest_futures_window(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def fetcher(symbol, interval, start_time_ms, end_time_ms, limit):
                calls.append((symbol, interval, limit))
                return [
                    [
                        _ms("2026-05-03T00:02:00Z"),
                        "1",
                        "3",
                        "0.5",
                        "2.5",
                        "10",
                        _ms("2026-05-03T00:02:59.999Z"),
                        "25",
                    ]
                ]

            with connect(db_path) as connection:
                instrument_id = InstrumentRepository(connection).upsert(
                    binance_futures_symbol_to_instrument({"symbol": "ETHUSDT"})
                )
                IntradayBarRepository(connection).upsert(
                    IntradayBar(
                        instrument_id=instrument_id,
                        interval="1m",
                        bar_start_ts_utc="2026-05-03T00:00:00Z",
                        bar_end_ts_utc="2026-05-03T00:01:00Z",
                        trade_date_local="2026-05-03",
                        open=1,
                        high=2,
                        low=0.5,
                        close=1.5,
                        volume_raw=10,
                        turnover_raw=15,
                        is_closed_bar=True,
                        source="existing",
                    )
                )
                payload = get_intraday_bars_payload(
                    connection,
                    "CRYPTO_FUTURES",
                    "ETHUSDT",
                    "1m",
                    now_ts_utc="2026-05-03T00:03:15Z",
                    gap_fetcher=fetcher,
                    gap_min_request_interval_seconds=0,
                )

        self.assertEqual(payload["items"][-1]["bar_start_ts_utc"], "2026-05-03T00:02:00Z")
        self.assertEqual(payload["items"][-1]["high"], 3.0)
        self.assertEqual(calls[-1], ("ETHUSDT", "1m", 3))

    def test_get_intraday_bars_payload_defaults_to_futures_fetcher_for_futures(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def futures_fetcher(symbol, interval, start_time_ms, end_time_ms, limit):
                calls.append((symbol, interval, limit))
                return [
                    [
                        _ms("2026-05-03T00:00:00Z"),
                        "1",
                        "2",
                        "0.5",
                        "1.5",
                        "10",
                        _ms("2026-05-03T00:00:59.999Z"),
                        "15",
                    ]
                ]

            with connect(db_path) as connection, patch(
                "market.api.fetch_binance_futures_klines_range",
                futures_fetcher,
            ):
                InstrumentRepository(connection).upsert(
                    binance_futures_symbol_to_instrument({"symbol": "ETHUSDT"})
                )
                payload = get_intraday_bars_payload(
                    connection,
                    "CRYPTO_FUTURES",
                    "ETHUSDT",
                    "1m",
                    now_ts_utc="2026-05-03T00:01:00Z",
                    gap_min_request_interval_seconds=0,
                )

        self.assertEqual(payload["items"][0]["source"], "binance_futures_gap_fill")
        self.assertEqual(calls, [("ETHUSDT", "1m", 1000)])

    def test_get_watchlists_payload_returns_active_entries_with_instruments(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                sync_watchlist_from_file(connection, Path("config/watchlists/etf_focus20.json"))
                payload = get_watchlists_payload(connection)

        self.assertEqual(payload["watchlists"][0]["watchlist_name"], "ETF_FOCUS20")
        self.assertEqual(payload["watchlists"][0]["items"][0]["symbol"], "SPY")
        self.assertEqual(payload["watchlists"][0]["items"][0]["sort_order"], 1)

    def test_get_health_payload_reports_database_and_latest_board(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                RankingRepository(connection).refresh_turnover_board(
                    board_name="CRYPTO_TURNOVER_TOP50",
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                    market="CRYPTO",
                    instrument_type="crypto",
                    limit=50,
                )
                payload = get_health_payload(connection)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["database"]["writable"], True)
        self.assertEqual(payload["latest_boards"][0]["board_name"], "CRYPTO_TURNOVER_TOP50")
        self.assertEqual(payload["latest_boards"][0]["item_count"], 2)

    def test_get_health_payload_counts_only_latest_board_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                ranking = RankingRepository(connection)
                ranking.refresh_turnover_board(
                    board_name="CRYPTO_TURNOVER_TOP50",
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                    market="CRYPTO",
                    instrument_type="crypto",
                    limit=50,
                )
                ranking.refresh_turnover_board(
                    board_name="CRYPTO_TURNOVER_TOP50",
                    snapshot_ts_utc="2026-04-24T20:05:00Z",
                    trade_date_local="2026-04-24",
                    market="CRYPTO",
                    instrument_type="crypto",
                    limit=1,
                )
                payload = get_health_payload(connection)

        self.assertEqual(payload["latest_boards"][0]["snapshot_ts_utc"], "2026-04-24T20:05:00Z")
        self.assertEqual(payload["latest_boards"][0]["item_count"], 1)

    def test_get_jobs_payload_returns_job_and_source_health_rows(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO job_state (
                        job_name,
                        checkpoint,
                        status,
                        last_started_at,
                        last_finished_at,
                        last_error
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "sync-crypto-board",
                        "BTCUSDT:15m",
                        "success",
                        "2026-04-24T20:00:00Z",
                        "2026-04-24T20:00:03Z",
                        None,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO source_health (
                        source_name,
                        status,
                        last_success_at,
                        last_error_at,
                        last_error
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("binance", "ok", "2026-04-24T20:00:03Z", None, None),
                )
                payload = get_jobs_payload(connection)

        self.assertEqual(payload["jobs"][0]["job_name"], "sync-crypto-board")
        self.assertEqual(payload["jobs"][0]["status"], "success")
        self.assertEqual(payload["sources"][0]["source_name"], "binance")
        self.assertEqual(payload["sources"][0]["status"], "ok")

    def test_get_alert_rules_payload_returns_rules(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                AlertRuleRepository(connection).upsert(
                    AlertRule(
                        name="btc change",
                        market="CRYPTO",
                        symbol="BTCUSDT",
                        metric="change_pct",
                        operator=">=",
                        threshold=2.0,
                    )
                )
                payload = get_alert_rules_payload(connection)

        self.assertEqual(payload["rules"][0]["name"], "btc change")
        self.assertEqual(payload["rules"][0]["metric"], "change_pct")

    def test_get_alert_metrics_payload_returns_supported_metrics(self):
        payload = get_alert_metrics_payload()

        keys = [metric["key"] for metric in payload["metrics"]]
        self.assertIn("change_pct", keys)
        self.assertIn("turnover_raw", keys)
        self.assertIn("volume_raw", keys)
        self.assertIn("last_price", keys)
        self.assertIn("MA", payload["chart_indicators"])
        self.assertIn("MACD", payload["chart_indicators"])

    def test_get_alert_events_payload_returns_recent_events(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                rule_id = AlertRuleRepository(connection).upsert(
                    AlertRule(
                        name="btc turnover",
                        market="CRYPTO",
                        symbol="BTCUSDT",
                        metric="turnover_raw",
                        operator=">=",
                        threshold=1.0,
                    )
                )
                instrument = get_instrument_payload(connection, "CRYPTO", "BTCUSDT")
                assert instrument is not None
                AlertEventRepository(connection).insert(
                    AlertEvent(
                        rule_id=rule_id,
                        rule_name="btc turnover",
                        instrument_id=int(instrument["instrument_id"]),
                        market="CRYPTO",
                        symbol="BTCUSDT",
                        triggered_at_utc="2026-04-24T20:01:00Z",
                        metric="turnover_raw",
                        observed_value=100.0,
                        threshold=1.0,
                        message="BTCUSDT turnover_raw 100.0 >= 1.0",
                    )
                )
                payload = get_alert_events_payload(connection, limit=5)

        self.assertEqual(payload["events"][0]["rule_name"], "btc turnover")
        self.assertEqual(payload["events"][0]["symbol"], "BTCUSDT")

    def test_get_static_asset_returns_mobile_dashboard_index(self):
        asset = get_static_asset("/")

        self.assertIsNotNone(asset)
        self.assertEqual(asset.content_type, "text/html; charset=utf-8")
        self.assertIn(b'<main class="shell">', asset.body)
        self.assertIn(b"ETF_FOCUS20", asset.body)
        self.assertIn(b'href="/status.html"', asset.body)

    def test_get_static_asset_returns_crypto_futures_board_tabs(self):
        asset = get_static_asset("/index.html")
        script = get_static_asset("/app.js")

        self.assertIn(b'data-section="ETF"', asset.body)
        self.assertIn(b'data-section="CRYPTO"', asset.body)
        self.assertIn(b'id="cryptoSubtabs"', asset.body)
        self.assertIn(b'data-board="CRYPTO_TURNOVER_TOP50"', asset.body)
        self.assertIn(b'data-board="CRYPTO_FUTURES_TURNOVER_TOP50"', asset.body)
        self.assertIn(b'data-board="CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50"', asset.body)
        self.assertIn(b"const CRYPTO_BOARDS =", script.body)
        self.assertIn(b'CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50: "TradeFi"', script.body)

    def test_get_static_asset_returns_instrument_detail_page(self):
        asset = get_static_asset("/instrument.html")

        self.assertIsNotNone(asset)
        self.assertEqual(asset.content_type, "text/html; charset=utf-8")
        self.assertIn(b'<section class="detail-panel"', asset.body)
        self.assertIn(b'id="klineChart"', asset.body)
        self.assertIn(b'id="periodTabs"', asset.body)
        self.assertIn(b'id="loadMoreBars"', asset.body)
        self.assertIn(b'id="chartRangeHint"', asset.body)
        self.assertIn(b'id="chartTimezone"', asset.body)
        self.assertIn(b"klinecharts@9.8.12", asset.body)
        self.assertIn(b"KLineCharts", asset.body)
        self.assertIn(b'id="volume"', asset.body)
        self.assertIn(b'id="intradayTitle"', asset.body)
        self.assertIn(b'/instrument.js?v=', asset.body)
        self.assertNotIn(b'id="dailyBars"', asset.body)
        self.assertNotIn(b'id="intradayBars"', asset.body)

    def test_get_static_asset_returns_klinecharts_indicator_setup(self):
        asset = get_static_asset("/instrument.js")

        self.assertIsNotNone(asset)
        self.assertEqual(asset.content_type, "text/javascript; charset=utf-8")
        self.assertIn(b'createIndicator("MA"', asset.body)
        self.assertIn(b'createIndicator("VOL"', asset.body)
        self.assertIn(b'createIndicator("MACD"', asset.body)
        self.assertIn(b"renderPeriodTabs", asset.body)
        self.assertIn(b"chartTimezone", asset.body)
        self.assertIn(b'["1m", "5m", "15m", "8h"]', asset.body)
        self.assertIn(b'market === "CRYPTO" || market === "CRYPTO_FUTURES"', asset.body)
        self.assertIn(b"DEFAULT_VISIBLE_CANDLES", asset.body)
        self.assertIn(b"LOAD_MORE_CANDLES", asset.body)
        self.assertIn(b"getVisibleCandles", asset.body)
        self.assertIn(b"pricePrecisionFromTickSize", asset.body)
        self.assertIn(b"formatPrice(snapshot.last_price, activePriceTickSize)", asset.body)
        self.assertIn(b"formatRawPrice", asset.body)
        self.assertIn(b'replace(/0+$/, "")', asset.body)
        self.assertIn(b"setPriceVolumePrecision", asset.body)
        self.assertIn(b"fetchOlderBars", asset.body)
        self.assertIn(b"setupChartHistoryLoader", asset.body)
        self.assertIn(b"setLoadDataCallback", asset.body)
        self.assertIn(b'params.type === "forward"', asset.body)
        self.assertIn(b"applyNewData(data)", asset.body)
        self.assertNotIn(b"applyNewData(data, true)", asset.body)
        self.assertIn(b"loadMoreBars", asset.body)
        self.assertIn(b"chartRangeHint", asset.body)
        self.assertNotIn(b"handleChartSwipe", asset.body)
        self.assertNotIn(b"pointerdown", asset.body)
        self.assertNotIn(b"pointerup", asset.body)
        self.assertNotIn(b"touchstart", asset.body)
        self.assertNotIn(b"touchend", asset.body)
        self.assertIn(b"loadLatestSnapshot", asset.body)
        self.assertIn(b"setInterval(loadLatestSnapshot, 5000)", asset.body)

    def test_get_static_asset_returns_board_rank_change_renderer(self):
        asset = get_static_asset("/app.js")

        self.assertIsNotNone(asset)
        assert asset is not None
        self.assertIn(b"formatRankChange", asset.body)
        self.assertIn(b"last_price", asset.body)
        self.assertIn(b"previous_snapshot_ts_utc", asset.body)
        self.assertIn(b"rank-change", asset.body)
        self.assertIn(b"previousPrices", asset.body)
        self.assertIn(b"pricePrecisionFromTickSize", asset.body)
        self.assertIn(b"formatPrice(item.last_price, item.price_tick_size)", asset.body)
        self.assertIn(b"return String(value)", asset.body)
        self.assertIn(b'replace(/0+$/, "")', asset.body)
        self.assertIn(b"formatPriceDirection", asset.body)
        self.assertIn(b"price-direction", asset.body)
        self.assertIn(b"setInterval(() => loadBoard(activeBoard, { silent: true })", asset.body)
        self.assertIn("排名较上期".encode("utf-8"), asset.body)
        self.assertIn("上期".encode("utf-8"), asset.body)
        self.assertIn(b"24h", asset.body)
        self.assertNotIn("价 ${formatPrice".encode("utf-8"), asset.body)
        self.assertNotIn("24h额".encode("utf-8"), asset.body)

    def test_get_static_asset_returns_status_page(self):
        asset = get_static_asset("/status.html")

        self.assertIsNotNone(asset)
        self.assertEqual(asset.content_type, "text/html; charset=utf-8")
        self.assertIn(b'id="healthStatus"', asset.body)
        self.assertIn(b'id="watchlistList"', asset.body)
        self.assertIn(b'id="jobList"', asset.body)
        self.assertIn(b'id="alertList"', asset.body)

    def test_get_static_asset_returns_status_alert_loader(self):
        asset = get_static_asset("/status.js")

        self.assertIsNotNone(asset)
        self.assertEqual(asset.content_type, "text/javascript; charset=utf-8")
        self.assertIn(b"/api/alerts/metrics", asset.body)
        self.assertIn(b"/api/alerts/rules", asset.body)
        self.assertIn(b"/api/alerts/events", asset.body)
        self.assertIn(b"renderAlertMetrics", asset.body)
        self.assertIn(b"renderAlertRules", asset.body)
        self.assertIn(b"renderAlerts", asset.body)

    def test_get_static_asset_rejects_unknown_paths(self):
        self.assertIsNone(get_static_asset("/missing.js"))


def _ms(value: str) -> int:
    normalized = value.replace("Z", "+00:00")
    return int(datetime.fromisoformat(normalized).astimezone(UTC).timestamp() * 1000)


if __name__ == "__main__":
    unittest.main()
