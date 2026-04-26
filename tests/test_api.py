from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.api import (
    get_daily_bars_payload,
    get_instrument_payload,
    get_intraday_bars_payload,
    get_board_payload,
    get_static_asset,
)
from market.db import connect, init_database
from market.repositories import RankingRepository
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

    def test_get_board_payload_returns_empty_items_for_unknown_board(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                payload = get_board_payload(connection, "MISSING_BOARD")

        self.assertEqual(payload["board_name"], "MISSING_BOARD")
        self.assertIsNone(payload["snapshot_ts_utc"])
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

    def test_get_static_asset_returns_mobile_dashboard_index(self):
        asset = get_static_asset("/")

        self.assertIsNotNone(asset)
        self.assertEqual(asset.content_type, "text/html; charset=utf-8")
        self.assertIn(b'<main class="shell">', asset.body)
        self.assertIn(b"ETF_FOCUS20", asset.body)

    def test_get_static_asset_returns_instrument_detail_page(self):
        asset = get_static_asset("/instrument.html")

        self.assertIsNotNone(asset)
        self.assertEqual(asset.content_type, "text/html; charset=utf-8")
        self.assertIn(b'<section class="detail-panel"', asset.body)
        self.assertIn(b'id="klineChart"', asset.body)
        self.assertIn(b'id="periodTabs"', asset.body)
        self.assertIn(b'id="chartTimezone"', asset.body)
        self.assertIn(b"klinecharts@9.8.12", asset.body)
        self.assertIn(b"KLineCharts", asset.body)
        self.assertIn(b'id="volume"', asset.body)
        self.assertIn(b'id="intradayTitle"', asset.body)
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

    def test_get_static_asset_rejects_unknown_paths(self):
        self.assertIsNone(get_static_asset("/missing.js"))


if __name__ == "__main__":
    unittest.main()
