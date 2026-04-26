from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
from market.db import connect, init_database
from market.repositories import RankingRepository
from market.models import AlertRule
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
        self.assertIn(b'["1m", "5m", "15m", "8h"]', asset.body)
        self.assertIn(b"DEFAULT_VISIBLE_CANDLES", asset.body)
        self.assertIn(b"getVisibleCandles", asset.body)
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


if __name__ == "__main__":
    unittest.main()
