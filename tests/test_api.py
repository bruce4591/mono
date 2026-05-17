from __future__ import annotations

import gzip
import io
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import market.api as api_module
from market.api import (
    _make_handler,
    _close_device_session,
    _open_device_session,
    _touch_device_session,
    get_alert_events_payload,
    get_alert_metrics_payload,
    get_alert_rules_payload,
    get_daily_bars_payload,
    format_mobile_alert_sse_events,
    mobile_alert_sse_heartbeat,
    get_health_payload,
    get_instrument_payload,
    get_instrument_detail_payload,
    get_intraday_bars_payload,
    get_jobs_payload,
    get_board_payload,
    get_static_asset,
    get_watchlists_payload,
    _finish_board_refresh,
    _reserve_board_refresh,
    _reserve_durable_board_refresh,
    refresh_board_prices_on_open,
)
from market.binance import binance_symbol_to_instrument
from market.binance_futures import binance_futures_symbol_to_instrument
from market.db import connect, init_database
from market.collectors.base import CollectorResult
from market.repositories import InstrumentRepository, IntradayBarRepository, RankingRepository
from market.models import AlertRule, Instrument, IntradayBar, MarketSnapshot
from market.repositories import AlertEventRepository, AlertRuleRepository
from market.models import AlertEvent
from market.repositories import MarketSnapshotRepository
from market.sample_data import seed_sample_data
from market.watchlists import sync_watchlist_from_file


class ApiTests(unittest.TestCase):
    def test_read_only_canary_blocks_mobile_writes_for_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            response_status, response_body = _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "push_token": "ExponentPushToken[test-token]",
                },
                read_only_canary=True,
            )
            with connect(db_path) as connection:
                device_count = connection.execute("SELECT count(*) FROM push_device").fetchone()[0]

        self.assertEqual(response_status, 503, response_body)
        self.assertEqual(device_count, 0)

    def test_postgres_handler_is_writable_unless_canary_flag_is_set(self):
        body = json.dumps(
            {
                "platform": "android",
                "push_token": "ExponentPushToken[test-token]",
            }
        ).encode("utf-8")
        request = (
            "POST /api/mobile/devices HTTP/1.1\r\n"
            "Host: testserver\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "\r\n"
        ).encode("utf-8") + body
        socket = _FakeSocket(request)
        fake_connection = object()
        fake_context = Mock()
        fake_context.__enter__ = Mock(return_value=fake_connection)
        fake_context.__exit__ = Mock(return_value=False)

        connector = Mock(return_value=fake_context)
        with patch("market.api.create_database_connector", return_value=connector):
            with patch(
                "market.api.register_mobile_device",
                return_value={"push_device_id": 1},
            ) as register_mobile_device:
                _make_handler("postgresql://market_app:secret@127.0.0.1:5432/market")(
                    socket,
                    ("127.0.0.1", 0),
                    object(),
                )

        raw_response = socket.response.getvalue()
        header, _, response_body = raw_response.partition(b"\r\n\r\n")
        status = int(header.split(b" ", 2)[1])
        self.assertEqual(status, 200, response_body)
        register_mobile_device.assert_called_once()

    def test_postgres_handler_creates_database_connector_once(self):
        with patch("market.api.create_database_connector") as create_connector:
            handler = _make_handler(
                "postgresql://market_app:secret@127.0.0.1:5432/market"
            )

        self.assertTrue(issubclass(handler, object))
        create_connector.assert_called_once_with(
            "postgresql://market_app:secret@127.0.0.1:5432/market"
        )

    def test_register_mobile_device_upserts_push_token(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            response_status, response_body = _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "push_token": "ExponentPushToken[test-token]",
                    "getui_cid": "getui-cid-1",
                    "device_label": "OnePlus 13T",
                },
            )

            self.assertEqual(response_status, 200, response_body)
            with connect(db_path) as connection:
                rows = connection.execute(
                    "SELECT platform, push_token, getui_cid, device_label, enabled FROM push_device"
                ).fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["platform"], "android")
        self.assertEqual(rows[0]["push_token"], "ExponentPushToken[test-token]")
        self.assertEqual(rows[0]["getui_cid"], "getui-cid-1")
        self.assertEqual(rows[0]["device_label"], "OnePlus 13T")
        self.assertEqual(rows[0]["enabled"], 1)

    def test_register_mobile_device_returns_existing_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "push_token": "ExponentPushToken[test-token]",
                    "device_label": "OnePlus 13T",
                },
            )
            with connect(db_path) as connection:
                push_device_id = int(
                    connection.execute(
                        "SELECT push_device_id FROM push_device"
                    ).fetchone()["push_device_id"]
                )
                connection.execute(
                    """
                    INSERT INTO device_checkpoint (
                        push_device_id,
                        last_seen_mobile_alert_event_id,
                        last_ack_mobile_alert_event_id,
                        updated_at_utc
                    )
                    VALUES (?, 7, 6, '2026-05-05T00:00:00Z')
                    """,
                    (push_device_id,),
                )
            response_status, response_body = _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "push_token": "ExponentPushToken[test-token]",
                    "device_label": "OnePlus 13T",
                },
            )

        self.assertEqual(response_status, 200, response_body)
        payload = json.loads(response_body)
        self.assertEqual(payload["checkpoint"]["last_seen_mobile_alert_event_id"], 7)
        self.assertEqual(payload["checkpoint"]["last_ack_mobile_alert_event_id"], 6)

    def test_board_open_refresh_uses_durable_refresh_state(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            with connect(db_path) as connection:
                first = _reserve_durable_board_refresh(
                    connection,
                    board_name="HK_STOCK_FOCUS20",
                    now_utc="2026-05-05T03:01:04+00:00",
                    ttl_seconds=60,
                )
                second = _reserve_durable_board_refresh(
                    connection,
                    board_name="HK_STOCK_FOCUS20",
                    now_utc="2026-05-05T03:01:30+00:00",
                    ttl_seconds=60,
                )

        self.assertTrue(first)
        self.assertFalse(second)

    def test_register_mobile_device_accepts_getui_only_device(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            response_status, response_body = _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "getui_cid": "getui-cid-1",
                    "device_label": "OnePlus 13T",
                },
            )

            self.assertEqual(response_status, 200, response_body)
            with connect(db_path) as connection:
                row = connection.execute(
                    "SELECT platform, push_token, getui_cid, device_label, enabled FROM push_device"
                ).fetchone()

        self.assertEqual(row["platform"], "android")
        self.assertEqual(row["push_token"], "getui:getui-cid-1")
        self.assertEqual(row["getui_cid"], "getui-cid-1")
        self.assertEqual(row["device_label"], "OnePlus 13T")
        self.assertEqual(row["enabled"], 1)

    def test_create_mobile_alert_rule_for_registered_device(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "push_token": "ExponentPushToken[test-token]",
                    "device_label": "OnePlus 13T",
                },
            )
            response_status, response_body = _request_api(
                db_path,
                "POST",
                "/api/mobile/alert-rules",
                {
                    "push_token": "ExponentPushToken[test-token]",
                    "market": "CRYPTO",
                    "symbol": "BTCUSDT",
                    "condition_type": "price_above",
                    "threshold": 68000.0,
                    "cooldown_seconds": 600,
                },
            )

            self.assertEqual(response_status, 200, response_body)
            payload = json.loads(response_body)
            self.assertEqual(payload["market"], "CRYPTO")
            self.assertEqual(payload["symbol"], "BTCUSDT")
            self.assertEqual(payload["condition_type"], "price_above")
            self.assertEqual(payload["threshold"], 68000.0)
            self.assertEqual(payload["cooldown_seconds"], 600)
            self.assertEqual(payload["enabled"], True)
            self.assertEqual(payload["source_type"], "builtin")
            self.assertEqual(payload["metric_key"], "last_price")
            self.assertEqual(payload["operator"], ">")

    def test_create_mobile_alert_rule_for_custom_indicator(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "push_token": "ExponentPushToken[test-token]",
                    "device_label": "OnePlus 13T",
                },
            )
            with connect(db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO indicator_definition (
                        name,
                        description,
                        expression,
                        input_scope,
                        created_by,
                        enabled,
                        created_at_utc,
                        updated_at_utc
                    )
                    VALUES ('volume pressure', '', 'volume_raw / max(turnover_raw, 1)', 'instrument', 'manual', 1, ?, ?)
                    """,
                    ("2026-05-05T00:00:00Z", "2026-05-05T00:00:00Z"),
                )
                indicator_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])

            response_status, response_body = _request_api(
                db_path,
                "POST",
                "/api/mobile/alert-rules",
                {
                    "push_token": "ExponentPushToken[test-token]",
                    "market": "CRYPTO",
                    "symbol": "BTCUSDT",
                    "source_type": "custom_indicator",
                    "indicator_id": indicator_id,
                    "operator": ">",
                    "threshold": 2.5,
                },
            )

        self.assertEqual(response_status, 200, response_body)
        payload = json.loads(response_body)
        self.assertEqual(payload["condition_type"], "custom_indicator")
        self.assertEqual(payload["source_type"], "custom_indicator")
        self.assertEqual(payload["metric_key"], "indicator_value")
        self.assertEqual(payload["operator"], ">")
        self.assertEqual(payload["indicator_id"], indicator_id)

    def test_get_mobile_alert_rules_filters_by_push_token(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "push_token": "ExponentPushToken[test-token]",
                    "device_label": "OnePlus 13T",
                },
            )
            _request_api(
                db_path,
                "POST",
                "/api/mobile/alert-rules",
                {
                    "push_token": "ExponentPushToken[test-token]",
                    "market": "CRYPTO",
                    "symbol": "BTCUSDT",
                    "condition_type": "change_pct_above",
                    "threshold": 2.0,
                },
            )
            response_status, response_body = _request_api(
                db_path,
                "GET",
                "/api/mobile/alert-rules?push_token=ExponentPushToken%5Btest-token%5D",
                {},
            )

        self.assertEqual(response_status, 200, response_body)
        payload = json.loads(response_body)
        self.assertEqual(len(payload["rules"]), 1)
        self.assertEqual(payload["rules"][0]["symbol"], "BTCUSDT")
        self.assertEqual(payload["rules"][0]["condition_type"], "change_pct_above")

    def test_patch_mobile_alert_rule_disables_rule(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "push_token": "ExponentPushToken[test-token]",
                    "device_label": "OnePlus 13T",
                },
            )
            _, create_body = _request_api(
                db_path,
                "POST",
                "/api/mobile/alert-rules",
                {
                    "push_token": "ExponentPushToken[test-token]",
                    "market": "CRYPTO",
                    "symbol": "BTCUSDT",
                    "condition_type": "price_below",
                    "threshold": 60000.0,
                },
            )
            rule_id = json.loads(create_body)["mobile_alert_rule_id"]
            response_status, response_body = _request_api(
                db_path,
                "PATCH",
                f"/api/mobile/alert-rules/{rule_id}",
                {"enabled": False},
            )

            self.assertEqual(response_status, 200, response_body)
            payload = json.loads(response_body)
            self.assertEqual(payload["enabled"], False)

    def test_get_mobile_alert_events_after_id_filters_by_device(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "push_token": "ExponentPushToken[test-token]",
                    "device_label": "OnePlus 13T",
                },
            )
            _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "push_token": "ExponentPushToken[other-token]",
                    "device_label": "Other",
                },
            )
            _, create_body = _request_api(
                db_path,
                "POST",
                "/api/mobile/alert-rules",
                {
                    "push_token": "ExponentPushToken[test-token]",
                    "market": "CRYPTO",
                    "symbol": "BTCUSDT",
                    "condition_type": "price_above",
                    "threshold": 68000.0,
                },
            )
            target_rule_id = json.loads(create_body)["mobile_alert_rule_id"]
            _, other_body = _request_api(
                db_path,
                "POST",
                "/api/mobile/alert-rules",
                {
                    "push_token": "ExponentPushToken[other-token]",
                    "market": "CRYPTO",
                    "symbol": "ETHUSDT",
                    "condition_type": "price_above",
                    "threshold": 3000.0,
                },
            )
            other_rule_id = json.loads(other_body)["mobile_alert_rule_id"]
            with connect(db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO mobile_alert_event (
                        mobile_alert_rule_id,
                        triggered_at_utc,
                        observed_value,
                        message,
                        delivery_status
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        target_rule_id,
                        "2026-05-05T00:00:00Z",
                        69000.0,
                        "BTCUSDT last_price 69000 > 68000",
                        "sent",
                    ),
                )
                first_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
                connection.execute(
                    """
                    INSERT INTO mobile_alert_event (
                        mobile_alert_rule_id,
                        triggered_at_utc,
                        observed_value,
                        message,
                        delivery_status
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        target_rule_id,
                        "2026-05-05T00:01:00Z",
                        70000.0,
                        "BTCUSDT last_price 70000 > 68000",
                        "sent",
                    ),
                )
                expected_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
                connection.execute(
                    """
                    INSERT INTO mobile_alert_event (
                        mobile_alert_rule_id,
                        triggered_at_utc,
                        observed_value,
                        message,
                        delivery_status
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        other_rule_id,
                        "2026-05-05T00:02:00Z",
                        3100.0,
                        "ETHUSDT last_price 3100 > 3000",
                        "sent",
                    ),
                )
            response_status, response_body = _request_api(
                db_path,
                "GET",
                (
                    "/api/mobile/alert-events?"
                    "push_token=ExponentPushToken%5Btest-token%5D"
                    f"&after_id={first_id}"
                ),
                {},
            )

        self.assertEqual(response_status, 200, response_body)
        payload = json.loads(response_body)
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["mobile_alert_event_id"], expected_id)
        self.assertEqual(payload["events"][0]["title"], "BTCUSDT 价格提醒")
        self.assertEqual(payload["events"][0]["data"]["url"], "/instrument.html?market=CRYPTO&symbol=BTCUSDT")

    def test_format_mobile_alert_sse_events_outputs_alert_events(self):
        body = format_mobile_alert_sse_events(
            [
                {
                    "mobile_alert_event_id": 123,
                    "title": "BTCUSDT 价格提醒",
                    "body": "BTCUSDT last_price 70000 > 68000",
                }
            ]
        ).decode("utf-8")

        self.assertIn("event: alert", body)
        self.assertIn("id: 123", body)
        self.assertIn('"mobile_alert_event_id": 123', body)

    def test_mobile_alert_sse_heartbeat_is_comment_frame(self):
        self.assertEqual(mobile_alert_sse_heartbeat(), b": keep-alive\n\n")

    def test_device_session_lifecycle_marks_disconnect(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            with connect(db_path) as connection:
                _request_api(
                    db_path,
                    "POST",
                    "/api/mobile/devices",
                    {
                        "platform": "android",
                        "push_token": "ExponentPushToken[test-token]",
                    },
                )
                push_device_id = connection.execute(
                    "SELECT push_device_id FROM push_device"
                ).fetchone()["push_device_id"]
                _open_device_session(
                    connection,
                    session_id="session-1",
                    push_device_id=int(push_device_id),
                    transport="sse",
                    now_utc="2026-05-05T00:00:00Z",
                )
                _touch_device_session(
                    connection,
                    session_id="session-1",
                    now_utc="2026-05-05T00:00:10Z",
                )
                _close_device_session(
                    connection,
                    session_id="session-1",
                    now_utc="2026-05-05T00:00:20Z",
                )
                row = connection.execute(
                    """
                    SELECT transport, last_seen_at_utc, disconnected_at_utc
                    FROM device_session
                    WHERE session_id = 'session-1'
                    """
                ).fetchone()

        self.assertEqual(row["transport"], "sse")
        self.assertEqual(row["last_seen_at_utc"], "2026-05-05T00:00:20Z")
        self.assertEqual(row["disconnected_at_utc"], "2026-05-05T00:00:20Z")

    def test_ack_mobile_alert_events_updates_device_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "push_token": "ExponentPushToken[test-token]",
                    "device_label": "OnePlus 13T",
                },
            )
            response_status, response_body = _request_api(
                db_path,
                "POST",
                "/api/mobile/alert-events/ack",
                {
                    "push_token": "ExponentPushToken[test-token]",
                    "last_seen_mobile_alert_event_id": 12,
                    "last_ack_mobile_alert_event_id": 10,
                },
            )

            self.assertEqual(response_status, 200, response_body)
            payload = json.loads(response_body)
            self.assertEqual(payload["last_seen_mobile_alert_event_id"], 12)
            self.assertEqual(payload["last_ack_mobile_alert_event_id"], 10)
            with connect(db_path) as connection:
                row = connection.execute(
                    """
                    SELECT last_seen_mobile_alert_event_id, last_ack_mobile_alert_event_id
                    FROM device_checkpoint
                    """
                ).fetchone()

        self.assertEqual(row["last_seen_mobile_alert_event_id"], 12)
        self.assertEqual(row["last_ack_mobile_alert_event_id"], 10)

    def test_get_mobile_debug_push_device_returns_runtime_state(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "push_token": "ExponentPushToken[test-token]",
                    "getui_cid": "getui-cid-1",
                    "device_label": "OnePlus 13T",
                },
            )
            with connect(db_path) as connection:
                push_device_id = int(
                    connection.execute(
                        "SELECT push_device_id FROM push_device"
                    ).fetchone()["push_device_id"]
                )
                _insert_mobile_debug_state(connection, push_device_id=push_device_id)

            response_status, response_body = _request_api(
                db_path,
                "GET",
                "/api/mobile/debug/push-device?push_token=ExponentPushToken[test-token]",
                {},
            )

        self.assertEqual(response_status, 200, response_body)
        payload = json.loads(response_body)
        self.assertEqual(payload["device"]["getui_cid"], "getui-cid-1")
        self.assertEqual(payload["checkpoint"]["last_seen_mobile_alert_event_id"], 1)
        self.assertEqual(payload["sessions"][0]["transport"], "sse")
        self.assertEqual(payload["recent_events"][0]["symbol"], "BTCUSDT")
        self.assertEqual(payload["recent_deliveries"][0]["status"], "sent")

    def test_get_mobile_debug_deliveries_returns_recent_attempts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            _request_api(
                db_path,
                "POST",
                "/api/mobile/devices",
                {
                    "platform": "android",
                    "push_token": "ExponentPushToken[test-token]",
                    "getui_cid": "getui-cid-1",
                },
            )
            with connect(db_path) as connection:
                push_device_id = int(
                    connection.execute(
                        "SELECT push_device_id FROM push_device"
                    ).fetchone()["push_device_id"]
                )
                _insert_mobile_debug_state(connection, push_device_id=push_device_id)

            response_status, response_body = _request_api(
                db_path,
                "GET",
                "/api/mobile/debug/deliveries?push_token=ExponentPushToken[test-token]&limit=5",
                {},
            )

        self.assertEqual(response_status, 200, response_body)
        payload = json.loads(response_body)
        self.assertEqual(payload["device"]["push_token"], "ExponentPushToken[test-token]")
        self.assertEqual(payload["deliveries"][0]["channel"], "getui")
        self.assertEqual(payload["deliveries"][0]["event_delivery_status"], "sent")

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

    def test_refresh_board_prices_on_open_updates_us_etf_price_only(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            class FakeAlpacaCollector:
                def refresh_latest_prices(self, connection, *, watchlist_names, snapshot_ts_utc=None):
                    calls.append((watchlist_names, snapshot_ts_utc))
                    spy = get_instrument_payload(connection, "US", "SPY")
                    assert spy is not None
                    connection.execute(
                        """
                        UPDATE market_snapshot
                        SET last_price = 512.34,
                            snapshot_ts_utc = ?,
                            source = 'alpaca_latest_trade'
                        WHERE instrument_id = ?
                        """,
                        (snapshot_ts_utc, spy["instrument_id"]),
                    )

            with connect(db_path) as connection, patch(
                "market.api.AlpacaCollector",
                return_value=FakeAlpacaCollector(),
            ):
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
                refresh_board_prices_on_open(
                    connection,
                    "ETF_FOCUS20",
                    snapshot_ts_utc="2026-05-03T12:40:00Z",
                )
                payload = get_board_payload(connection, "ETF_FOCUS20")

        self.assertEqual(payload["items"][0]["symbol"], "SPY")
        self.assertEqual(calls, [(["ETF_FOCUS20"], "2026-05-03T12:40:00Z")])
        self.assertEqual(payload["items"][0]["last_price"], 512.34)
        self.assertEqual(payload["items"][0]["turnover_raw"], 36734400000.0)

    def test_refresh_board_prices_on_open_syncs_hk_board(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            class FakeAkshareCollector:
                def sync_focus(
                    self,
                    connection,
                    *,
                    watchlist_names,
                    days,
                    snapshot_ts_utc,
                    trade_date_local,
                ):
                    calls.append((watchlist_names, days, snapshot_ts_utc, trade_date_local))
                    instrument = connection.execute(
                        """
                        SELECT instrument_id, quote_currency
                        FROM instrument
                        WHERE market = 'HK'
                        ORDER BY symbol
                        LIMIT 1
                        """
                    ).fetchone()
                    assert instrument is not None
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
                            instrument["instrument_id"],
                            snapshot_ts_utc,
                            "2026-05-05",
                            64.9,
                            -0.61,
                            1000.0,
                            64900.0,
                            instrument["quote_currency"],
                            "akshare",
                        ),
                    )
                    return CollectorResult(
                        source_name="akshare",
                        items_synced=1,
                        metadata={"trade_date_local": "2026-05-05"},
                    )

            with connect(db_path) as connection:
                refreshed = refresh_board_prices_on_open(
                    connection,
                    "HK_STOCK_FOCUS20",
                    snapshot_ts_utc="2026-05-05T03:01:04Z",
                    akshare_collector_factory=FakeAkshareCollector,
                )
                payload = get_board_payload(connection, "HK_STOCK_FOCUS20")

        self.assertTrue(refreshed)
        self.assertEqual(calls, [(["HK_STOCK_FOCUS20"], 365, "2026-05-05T03:01:04Z", None)])
        self.assertEqual(payload["snapshot_ts_utc"], "2026-05-05T03:01:04Z")
        self.assertEqual(payload["items"][0]["market"], "HK")
        self.assertEqual(payload["items"][0]["last_price"], 64.9)

    def test_board_refresh_reservation_enforces_one_minute_ttl(self):
        self.assertTrue(_reserve_board_refresh("HK_STOCK_FOCUS20", 100.0, 60))
        self.assertFalse(_reserve_board_refresh("HK_STOCK_FOCUS20", 130.0, 60))
        _finish_board_refresh("HK_STOCK_FOCUS20")
        self.assertFalse(_reserve_board_refresh("HK_STOCK_FOCUS20", 159.0, 60))
        self.assertTrue(_reserve_board_refresh("HK_STOCK_FOCUS20", 160.0, 60))
        _finish_board_refresh("HK_STOCK_FOCUS20")

    def test_get_board_payload_returns_volume_change_from_previous_trade_date(self):
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
                assert btc is not None
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
                        btc["instrument_id"],
                        "2026-04-23T20:00:00Z",
                        "2026-04-23",
                        60000.0,
                        1.0,
                        21000.0,
                        1_260_000_000.0,
                        "USDT",
                        "test",
                    ),
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
        self.assertEqual(payload["items"][0]["volume_change_pct"], 100.0)

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

    def test_get_mobile_home_payload_returns_boards(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                payload = api_module.get_mobile_home_payload(connection)

        self.assertIn("server_time", payload)
        self.assertIn("boards", payload)
        self.assertGreaterEqual(len(payload["boards"]), 1)
        first_board = payload["boards"][0]
        self.assertIn("key", first_board)
        self.assertIn("title", first_board)
        self.assertIn("items", first_board)
        if first_board["items"]:
            first_item = first_board["items"][0]
            self.assertIn("name", first_item)
            self.assertIn("last_price", first_item)
            self.assertIn("data_time", first_item)

    def test_get_mobile_home_payload_returns_macro_rates_and_fx_boards(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instruments = InstrumentRepository(connection)
                snapshots = MarketSnapshotRepository(connection)
                us10y_id = instruments.upsert(
                    Instrument(
                        market="MACRO_RATE",
                        symbol="US10Y",
                        display_name="US Treasury 10Y",
                        exchange="AKSHARE",
                        instrument_type="yield",
                        quote_currency="PCT",
                        timezone="UTC",
                    )
                )
                usdcny_id = instruments.upsert(
                    Instrument(
                        market="FX",
                        symbol="USDCNY",
                        display_name="USD/CNY",
                        exchange="SAFE",
                        instrument_type="fx",
                        quote_currency="CNY",
                        timezone="Asia/Shanghai",
                    )
                )
                snapshots.upsert(
                    MarketSnapshot(
                        instrument_id=us10y_id,
                        snapshot_ts_utc="2026-05-15T21:00:00Z",
                        trade_date_local="2026-05-15",
                        last_price=4.441,
                        change_pct=0.18,
                        volume_raw=None,
                        turnover_raw=1.0,
                        quote_currency="PCT",
                        source="macro",
                    )
                )
                snapshots.upsert(
                    MarketSnapshot(
                        instrument_id=usdcny_id,
                        snapshot_ts_utc="2026-05-16T01:00:00Z",
                        trade_date_local="2026-05-16",
                        last_price=7.2195,
                        change_pct=-0.04,
                        volume_raw=None,
                        turnover_raw=1.0,
                        quote_currency="CNY",
                        source="macro",
                    )
                )
                payload = api_module.get_mobile_home_payload(connection)

        boards = {board["key"]: board for board in payload["boards"]}
        self.assertIn("RATES_FOCUS", boards)
        self.assertIn("FX_FOCUS", boards)
        self.assertEqual(boards["RATES_FOCUS"]["title"], "Rates")
        self.assertEqual(boards["FX_FOCUS"]["title"], "FX")
        self.assertEqual(boards["RATES_FOCUS"]["items"][0]["symbol"], "US10Y")
        self.assertEqual(boards["RATES_FOCUS"]["items"][0]["unit"], "%")
        self.assertEqual(boards["FX_FOCUS"]["items"][0]["symbol"], "USDCNY")
        self.assertIsNone(boards["FX_FOCUS"]["items"][0]["unit"])
        self.assertEqual(payload["boards"][0]["key"], "RATES_FOCUS")
        self.assertEqual(payload["boards"][1]["key"], "FX_FOCUS")

    def test_mobile_home_endpoint_returns_boards(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )

            response_status, response_body = _request_api(
                db_path,
                "GET",
                "/api/mobile/home",
                {},
            )

        self.assertEqual(response_status, 200, response_body)
        payload = json.loads(response_body)
        self.assertIn("server_time", payload)
        self.assertIn("boards", payload)

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

    def test_get_instrument_payload_includes_futures_funding_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                InstrumentRepository(connection).upsert(
                    binance_futures_symbol_to_instrument({"symbol": "ETHUSDT"})
                )
                payload = get_instrument_payload(
                    connection,
                    "CRYPTO_FUTURES",
                    "ETHUSDT",
                    include_funding=True,
                    futures_funding_fetcher=lambda symbol: {
                        "symbol": symbol,
                        "last_funding_rate": 0.0001,
                        "last_funding_rate_pct": 0.01,
                        "next_funding_time_utc": "2026-05-03T12:00:00Z",
                        "mark_price": 2301.25,
                        "index_price": 2300.5,
                        "source": "binance_futures_premium_index",
                    },
                )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["funding_rate"]["symbol"], "ETHUSDT")
        self.assertEqual(payload["funding_rate"]["last_funding_rate_pct"], 0.01)
        self.assertEqual(payload["funding_rate"]["next_funding_time_utc"], "2026-05-03T12:00:00Z")

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

    def test_get_instrument_detail_payload_returns_instrument_daily_and_periods(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                payload = get_instrument_detail_payload(
                    connection,
                    "US",
                    "SPY",
                    daily_limit=5,
                )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["instrument"]["symbol"], "SPY")
        self.assertEqual(payload["daily_bars"]["interval"], "1d")
        self.assertEqual(len(payload["daily_bars"]["items"]), 5)
        self.assertEqual(payload["intraday_bars"], [])
        self.assertEqual(payload["available_periods"], ["1d"])

    def test_get_mobile_instrument_detail_payload_returns_snapshot_and_bars(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                payload = api_module.get_mobile_instrument_detail_payload(
                    connection,
                    market="US",
                    symbol="SPY",
                    period="1d",
                    daily_limit=5,
                )

        self.assertEqual(payload["instrument"]["symbol"], "SPY")
        self.assertIn("snapshot", payload)
        self.assertIn("periods", payload)
        self.assertEqual(payload["periods"], ["1d"])
        self.assertEqual(len(payload["bars"]), 5)
        self.assertIn("time", payload["bars"][0])
        self.assertIn("turnover", payload["bars"][0])

    def test_mobile_macro_detail_backfills_daily_trend_on_open(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            def fake_sync_macro_boards(connection, **kwargs):
                from market.macro import sync_macro_boards

                return sync_macro_boards(
                    connection,
                    snapshot_ts_utc="2026-05-16T12:00:00Z",
                    bond_rate_frame=[
                        {
                            "日期": "2026-05-15",
                            "美国国债收益率2年": 4.40,
                            "美国国债收益率5年": 4.20,
                            "美国国债收益率10年": 4.50,
                            "美国国债收益率30年": 4.70,
                            "中国国债收益率10年": 1.75,
                            "中国国债收益率30年": 2.05,
                        },
                        {
                            "日期": "2026-05-16",
                            "美国国债收益率2年": 4.42,
                            "美国国债收益率5年": 4.23,
                            "美国国债收益率10年": 4.55,
                            "美国国债收益率30年": 4.74,
                            "中国国债收益率10年": 1.77,
                            "中国国债收益率30年": 2.08,
                        },
                    ],
                    fx_safe_frame=[
                        {
                            "日期": "2026-05-15",
                            "美元": 720.00,
                            "欧元": 828.00,
                            "日元": 4.80,
                            "英镑": 960.00,
                            "澳元": 468.00,
                            "加元": 525.00,
                        },
                        {
                            "日期": "2026-05-16",
                            "美元": 721.00,
                            "欧元": 829.15,
                            "日元": 4.85,
                            "英镑": 962.00,
                            "澳元": 470.00,
                            "加元": 524.00,
                        },
                    ],
                )

            with connect(db_path) as connection:
                with patch("market.api.sync_macro_boards", fake_sync_macro_boards):
                    payload = api_module.get_mobile_instrument_detail_payload(
                        connection,
                        market="MACRO_RATE",
                        symbol="US10Y",
                        period="1d",
                        daily_limit=5,
                    )

        self.assertEqual(payload["instrument"]["symbol"], "US10Y")
        self.assertEqual(payload["periods"], ["1d"])
        self.assertEqual(len(payload["bars"]), 2)
        self.assertEqual(payload["bars"][-1]["time"], "2026-05-16")
        self.assertEqual(payload["bars"][-1]["close"], 4.55)

    def test_get_mobile_instrument_detail_payload_returns_alert_markers_for_period(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                push_device_id = _insert_push_device(connection)
                connection.execute(
                    """
                    INSERT INTO mobile_alert_rule (
                        push_device_id,
                        symbol,
                        market,
                        condition_type,
                        source_type,
                        metric_key,
                        operator,
                        threshold,
                        cooldown_seconds,
                        enabled,
                        created_by,
                        created_at_utc,
                        updated_at_utc
                    )
                    VALUES (?, 'BTCUSDT', 'CRYPTO', 'ma11_breakout_1d',
                        'technical', 'ma11_cross', '>', 0.0, 86400, 1,
                        'system_crypto_ranking_ma11', ?, ?)
                    """,
                    (
                        push_device_id,
                        "2026-04-24T20:00:00Z",
                        "2026-04-24T20:00:00Z",
                    ),
                )
                rule_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO mobile_alert_rule (
                        push_device_id,
                        symbol,
                        market,
                        condition_type,
                        source_type,
                        metric_key,
                        operator,
                        threshold,
                        cooldown_seconds,
                        enabled,
                        created_by,
                        created_at_utc,
                        updated_at_utc
                    )
                    VALUES (?, 'BTCUSDT', 'CRYPTO', 'ma11_breakout_1d',
                        'technical', 'ma11_cross', '>', 0.0, 86400, 1,
                        'system_crypto_ranking_ma11', ?, ?)
                    """,
                    (
                        push_device_id,
                        "2026-04-24T20:00:00Z",
                        "2026-04-24T20:00:00Z",
                    ),
                )
                duplicate_rule_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO mobile_alert_event (
                        mobile_alert_rule_id,
                        triggered_at_utc,
                        observed_value,
                        message,
                        dedupe_key,
                        alert_metadata,
                        delivery_status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'sent')
                    """,
                    (
                        rule_id,
                        "2026-04-24 20:16:00+00:00",
                        65000.0,
                        "BTCUSDT 1d MA11 突破 close 65000 > MA11 64000，交易日 2026-04-24",
                        "ma11_breakout_1d:CRYPTO:BTCUSDT:2026-04-24",
                        '{"period":"1d","bar_time":"2026-04-24","price":65000.0,"direction":"up","label":"1d MA11 突破","condition_label":"1d MA11 突破","ma11":64000.0}',
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO mobile_alert_event (
                        mobile_alert_rule_id,
                        triggered_at_utc,
                        observed_value,
                        message,
                        dedupe_key,
                        alert_metadata,
                        delivery_status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'sent')
                    """,
                    (
                        duplicate_rule_id,
                        "2026-04-24T20:17:00Z",
                        65000.0,
                        "duplicate marker",
                        "duplicate-existing-rule:2026-04-24",
                        '{"period":"1d","bar_time":"2026-04-24","price":65000.0,"direction":"up","label":"1d MA11 突破","condition_label":"1d MA11 突破","ma11":64000.0}',
                    ),
                )

                payload = api_module.get_mobile_instrument_detail_payload(
                    connection,
                    market="CRYPTO",
                    symbol="BTCUSDT",
                    period="1d",
                    intraday_limit=10,
                    allow_backfill=False,
                )

        self.assertEqual(len(payload["alert_markers"]), 1)
        marker = payload["alert_markers"][0]
        self.assertEqual(marker["time"], "2026-04-24")
        self.assertEqual(marker["price"], 65000.0)
        self.assertEqual(marker["direction"], "up")
        self.assertEqual(marker["condition_label"], "1d MA11 突破")
        self.assertEqual(marker["ma11"], 64000.0)
        self.assertIsNone(marker["volume_ratio"])

    def test_get_mobile_instrument_detail_payload_projects_daily_ma11_markers_to_intraday_periods(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                push_device_id = _insert_push_device(connection)
                connection.execute(
                    """
                    INSERT INTO mobile_alert_rule (
                        push_device_id,
                        symbol,
                        market,
                        condition_type,
                        source_type,
                        metric_key,
                        operator,
                        threshold,
                        cooldown_seconds,
                        enabled,
                        created_by,
                        created_at_utc,
                        updated_at_utc
                    )
                    VALUES (?, 'BTCUSDT', 'CRYPTO', 'ma11_breakout_1d',
                        'technical', 'ma11_cross', '>', 0.0, 86400, 1,
                        'system_crypto_ranking_ma11', ?, ?)
                    """,
                    (
                        push_device_id,
                        "2026-04-24T20:00:00Z",
                        "2026-04-24T20:00:00Z",
                    ),
                )
                rule_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO mobile_alert_event (
                        mobile_alert_rule_id,
                        triggered_at_utc,
                        observed_value,
                        message,
                        dedupe_key,
                        alert_metadata,
                        delivery_status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'sent')
                    """,
                    (
                        rule_id,
                        "2026-04-24T20:16:00Z",
                        65000.0,
                        "BTCUSDT 1d MA11 突破 close 65000 > MA11 64000，交易日 2026-04-24",
                        "ma11_breakout_1d:CRYPTO:BTCUSDT:2026-04-24",
                        json.dumps(
                            {
                                "period": "1d",
                                "bar_time": "2026-04-24",
                                "price": 65000.0,
                                "direction": "up",
                                "label": "1d MA11 突破",
                                "condition_label": "1d MA11 突破",
                                "ma11": 64000.0,
                            }
                        ),
                    ),
                )

                payload = api_module.get_mobile_instrument_detail_payload(
                    connection,
                    market="CRYPTO",
                    symbol="BTCUSDT",
                    period="15m",
                    intraday_limit=10,
                    allow_backfill=False,
                )

        self.assertEqual(len(payload["alert_markers"]), 1)
        marker = payload["alert_markers"][0]
        self.assertEqual(marker["time"], "2026-04-24T20:16:00Z")
        self.assertEqual(marker["price"], 65000.0)
        self.assertEqual(marker["direction"], "up")
        self.assertEqual(marker["condition_type"], "ma11_breakout_1d")
        self.assertEqual(marker["condition_label"], "1d MA11 突破")
        self.assertEqual(marker["ma11"], 64000.0)

    def test_daily_ma11_intraday_marker_time_normalizes_postgres_timestamp(self):
        marker_time = api_module._mobile_alert_marker_time_for_chart(
            condition_type="ma11_breakout_1d",
            marker_period="1d",
            chart_period="15m",
            bar_time="2026-05-17",
            triggered_at_utc="2026-05-17 03:21:01+00:00",
        )

        self.assertEqual(marker_time, "2026-05-17T03:21:01Z")

    def test_get_mobile_instrument_detail_payload_returns_strategy_markers_on_chart_period(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = InstrumentRepository(connection).upsert(
                    binance_futures_symbol_to_instrument({"symbol": "BTCUSDT"})
                )
                IntradayBarRepository(connection).upsert(
                    IntradayBar(
                        instrument_id=instrument_id,
                        interval="1m",
                        bar_start_ts_utc="2026-04-12T13:21:00Z",
                        bar_end_ts_utc="2026-04-12T13:21:59Z",
                        trade_date_local="2026-04-12",
                        open=63950.0,
                        high=64050.0,
                        low=63900.0,
                        close=64000.0,
                        volume_raw=12.0,
                        turnover_raw=768000.0,
                        is_closed_bar=True,
                        source="binance_futures_ws",
                    )
                )
                push_device_id = _insert_push_device(connection)
                connection.execute(
                    """
                    INSERT INTO mobile_alert_rule (
                        push_device_id,
                        symbol,
                        market,
                        condition_type,
                        source_type,
                        metric_key,
                        operator,
                        threshold,
                        cooldown_seconds,
                        enabled,
                        created_by,
                        created_at_utc,
                        updated_at_utc
                    )
                    VALUES (?, 'BTCUSDT', 'CRYPTO_FUTURES', 'paper_strategy_pin',
                        'strategy', 'paper_trade', 'event', 0.0, 0, 1,
                        'system_strategy_crypto_pin_rebound_v1', ?, ?)
                    """,
                    (
                        push_device_id,
                        "2026-04-12T13:20:00Z",
                        "2026-04-12T13:20:00Z",
                    ),
                )
                rule_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO mobile_alert_event (
                        mobile_alert_rule_id,
                        triggered_at_utc,
                        observed_value,
                        message,
                        dedupe_key,
                        alert_metadata,
                        delivery_status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'sent')
                    """,
                    (
                        rule_id,
                        "2026-04-12T13:21:05Z",
                        64000.0,
                        "BTCUSDT Pin 模拟开多 成交价 64000",
                        "paper_strategy_pin:crypto_pin_rebound_v1:1",
                        json.dumps(
                            {
                                "chart_period": "1m",
                                "bar_time": "2026-04-12T13:21:05Z",
                                "price": 64000.0,
                                "direction": "up",
                                "label": "Pin 模拟开多",
                                "condition_label": "Pin 模拟开多",
                                "signal_timeframe": "realtime",
                                "strategy_id": "crypto_pin_rebound_v1",
                            }
                        ),
                    ),
                )

                payload = api_module.get_mobile_instrument_detail_payload(
                    connection,
                    market="CRYPTO_FUTURES",
                    symbol="BTCUSDT",
                    period="1m",
                    intraday_limit=5,
                    allow_backfill=False,
                )

        assert payload is not None
        self.assertEqual(len(payload["alert_markers"]), 1)
        marker = payload["alert_markers"][0]
        self.assertEqual(marker["time"], "2026-04-12T13:21:05Z")
        self.assertEqual(marker["price"], 64000.0)
        self.assertEqual(marker["direction"], "up")
        self.assertEqual(marker["condition_type"], "paper_strategy_pin")
        self.assertEqual(marker["condition_label"], "Pin 模拟开多")

    def test_instrument_detail_endpoint_returns_aggregated_chart_payload(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )

            response_status, response_body = _request_api(
                db_path,
                "GET",
                "/api/instrument-detail?market=CRYPTO&symbol=BTCUSDT&daily_limit=5&intraday_interval=15m&intraday_limit=6",
                {},
            )

        self.assertEqual(response_status, 200)
        payload = json.loads(response_body)
        self.assertEqual(payload["instrument"]["symbol"], "BTCUSDT")
        self.assertEqual(len(payload["daily_bars"]["items"]), 5)
        self.assertEqual(len(payload["intraday_bars"]), 1)
        self.assertEqual(payload["intraday_bars"][0]["interval"], "15m")
        self.assertEqual(payload["available_periods"], ["15m", "1d"])

    def test_mobile_instrument_detail_endpoint_returns_native_shape(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                get_instrument_payload(
                    connection,
                    "CRYPTO",
                    "BTCUSDT",
                    instrument_metadata_fetcher=lambda _symbol: {
                        "price_tick_size": "0.01000000",
                    },
                )

            response_status, response_body = _request_api(
                db_path,
                "GET",
                "/api/mobile/instrument-detail?market=CRYPTO&symbol=BTCUSDT&period=1d&daily_limit=5",
                {},
            )

        self.assertEqual(response_status, 200, response_body)
        payload = json.loads(response_body)
        self.assertEqual(payload["instrument"]["symbol"], "BTCUSDT")
        self.assertEqual(payload["instrument"]["price_tick_size"], "0.01000000")
        self.assertIn("snapshot", payload)
        self.assertIn("1d", payload["periods"])
        self.assertEqual(len(payload["bars"]), 5)

    def test_mobile_instrument_detail_endpoint_returns_available_intraday_periods_for_daily_request(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )

            response_status, response_body = _request_api(
                db_path,
                "GET",
                "/api/mobile/instrument-detail?market=CRYPTO&symbol=BTCUSDT&period=1d&daily_limit=5",
                {},
            )

        self.assertEqual(response_status, 200, response_body)
        payload = json.loads(response_body)
        self.assertEqual(payload["instrument"]["symbol"], "BTCUSDT")
        self.assertIn("15m", payload["periods"])
        self.assertIn("1d", payload["periods"])
        self.assertEqual(len(payload["bars"]), 5)

    def test_mobile_instrument_detail_endpoint_returns_intraday_bar_time(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )

            response_status, response_body = _request_api(
                db_path,
                "GET",
                "/api/mobile/instrument-detail?market=CRYPTO&symbol=BTCUSDT&period=15m&intraday_limit=2",
                {},
            )

        self.assertEqual(response_status, 200, response_body)
        payload = json.loads(response_body)
        self.assertEqual(payload["instrument"]["symbol"], "BTCUSDT")
        self.assertEqual(payload["bars"][0]["time"], "2026-04-24T15:00:00Z")

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
                calls.append((symbol, interval, start_time_ms, end_time_ms, limit))
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
                calls.append((symbol, interval, start_time_ms, end_time_ms, limit))
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
                    now_ts_utc="2026-05-03T05:39:00Z",
                    gap_fetcher=fetcher,
                    gap_min_request_interval_seconds=0,
                )

        self.assertEqual(payload["interval"], "8h")
        self.assertEqual(len(payload["items"]), 60)
        self.assertEqual(payload["items"][0]["source"], "binance_futures_gap_fill")
        self.assertEqual(
            calls,
            [
                (
                    "ETHUSDT",
                    "8h",
                    _ms("2026-04-13T08:00:00Z"),
                    _ms("2026-05-03T07:59:59.999Z"),
                    60,
                )
            ],
        )

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

    def test_get_daily_bars_payload_fetches_older_futures_daily_window(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def fetcher(symbol, interval, start_time_ms, end_time_ms, limit):
                calls.append((symbol, interval, start_time_ms, end_time_ms, limit))
                rows = []
                start_ms = _ms("2026-03-04T00:00:00Z")
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
                InstrumentRepository(connection).upsert(
                    binance_futures_symbol_to_instrument({"symbol": "ETHUSDT"})
                )
                payload = get_daily_bars_payload(
                    connection,
                    "CRYPTO_FUTURES",
                    "ETHUSDT",
                    before_trade_date="2026-05-03",
                    limit=60,
                    futures_fetcher=fetcher,
                )

        self.assertEqual(payload["interval"], "1d")
        self.assertEqual(len(payload["items"]), 60)
        self.assertEqual(payload["items"][0]["trade_date"], "2026-03-04")
        self.assertEqual(payload["items"][-1]["trade_date"], "2026-05-02")
        self.assertEqual(
            calls,
            [
                (
                    "ETHUSDT",
                    "1d",
                    _ms("2026-03-04T00:00:00Z"),
                    _ms("2026-05-02T23:59:59.999Z"),
                    60,
                )
            ],
        )

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

        self.assertIn(b"TradFi", asset.body)
        self.assertIn(b'data-section="TRADITIONAL"', asset.body)
        self.assertIn(b'data-section="CRYPTO"', asset.body)
        self.assertIn(b'id="traditionalSubtabs"', asset.body)
        self.assertIn(b'id="cryptoSubtabs"', asset.body)
        self.assertIn(b'data-board="A_SHARE_FOCUS20"', asset.body)
        self.assertIn(b'data-board="HK_STOCK_FOCUS20"', asset.body)
        self.assertIn(b'data-board="US_STOCK_FOCUS20"', asset.body)
        self.assertIn(b'data-board="ETF_FOCUS20"', asset.body)
        self.assertIn(b'data-board="INDEX_FOCUS20"', asset.body)
        self.assertIn(b'data-board="COMMODITY_FOCUS20"', asset.body)
        self.assertIn(b'data-board="CRYPTO_TURNOVER_TOP50"', asset.body)
        self.assertIn(b'data-board="CRYPTO_FUTURES_TURNOVER_TOP50"', asset.body)
        self.assertIn(b'data-board="CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50"', asset.body)
        self.assertIn(b"const TRADITIONAL_BOARDS =", script.body)
        self.assertIn(b'A_SHARE_FOCUS20: "A-SH"', script.body)
        self.assertIn(b'HK_STOCK_FOCUS20: "HK"', script.body)
        self.assertIn(b'US_STOCK_FOCUS20: "US"', script.body)
        self.assertIn(b'INDEX_FOCUS20: "IDX"', script.body)
        self.assertIn(b'COMMODITY_FOCUS20: "CMDTY"', script.body)
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
        self.assertIn(b"klinecharts.min.js\" async", asset.body)
        self.assertIn(b"KLineCharts", asset.body)
        self.assertIn(b'id="volume"', asset.body)
        self.assertIn(b'id="dataTime"', asset.body)
        self.assertIn(b'id="tradeDate"', asset.body)
        self.assertIn(b'id="dataSource"', asset.body)
        self.assertIn(b'id="fundingRatePanel"', asset.body)
        self.assertIn(b'id="fundingRate"', asset.body)
        self.assertIn(b'id="nextFundingTime"', asset.body)
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
        self.assertIn(b"waitForChartLibrary", asset.body)
        self.assertIn(b"renderRequestId", asset.body)
        self.assertIn(b"async function renderCandles", asset.body)
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
        self.assertIn(b"renderFundingRate", asset.body)
        self.assertIn(b"formatFundingRate", asset.body)
        self.assertIn(b"formatSnapshotTime", asset.body)
        self.assertIn(b"snapshot.snapshot_ts_utc", asset.body)
        self.assertIn(b"snapshot.trade_date_local", asset.body)
        self.assertIn(b"snapshot.source", asset.body)
        self.assertIn(b"fetchInstrumentDetail", asset.body)
        self.assertIn(b"/api/instrument-detail?", asset.body)
        self.assertIn(b"intraday_interval", asset.body)
        self.assertIn(b"fetchDailyBars", asset.body)
        self.assertNotIn(
            b"fetchDailyBars(DEFAULT_VISIBLE_CANDLES[\"1d\"] || 120)",
            asset.body,
        )
        self.assertIn(b"before_trade_date", asset.body)
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

    def test_static_asset_response_uses_http11_and_gzip_when_requested(self):
        request = (
            "GET /instrument.js HTTP/1.1\r\n"
            "Host: testserver\r\n"
            "Accept-Encoding: gzip\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("utf-8")
        socket = _FakeSocket(request)
        _make_handler(":memory:")(socket, ("127.0.0.1", 0), object())

        raw_response = socket.response.getvalue()
        header, _, response_body = raw_response.partition(b"\r\n\r\n")

        self.assertTrue(header.startswith(b"HTTP/1.1 200 OK"), header)
        self.assertIn(b"Content-Encoding: gzip", header)
        self.assertIn(b"Vary: Accept-Encoding", header)
        self.assertLess(len(response_body), 8_000)
        self.assertIn(b"fetchInstrumentDetail", gzip.decompress(response_body))

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
        self.assertIn(b"price-change", asset.body)
        self.assertIn(b"volume-change", asset.body)
        self.assertIn(b"volume_change_pct", asset.body)
        self.assertIn("量变化".encode("utf-8"), asset.body)
        self.assertIn(b"setInterval(() => loadBoard(activeBoard, { silent: true })", asset.body)
        self.assertIn("排名较上期".encode("utf-8"), asset.body)
        self.assertIn("上期".encode("utf-8"), asset.body)
        self.assertIn("价格 ".encode("utf-8"), asset.body)
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


def _insert_mobile_debug_state(
    connection: sqlite3.Connection,
    *,
    push_device_id: int,
) -> None:
    connection.execute(
        """
        INSERT INTO mobile_alert_rule (
            push_device_id,
            symbol,
            market,
            condition_type,
            threshold,
            cooldown_seconds,
            enabled,
            created_at_utc,
            updated_at_utc
        )
        VALUES (?, 'BTCUSDT', 'CRYPTO', 'price_above', 68000.0, 900, 1, ?, ?)
        """,
        (push_device_id, "2026-05-05T00:00:00Z", "2026-05-05T00:00:00Z"),
    )
    rule_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    connection.execute(
        """
        INSERT INTO mobile_alert_event (
            mobile_alert_rule_id,
            triggered_at_utc,
            observed_value,
            message,
            delivery_status
        )
        VALUES (?, ?, 69000.0, 'BTCUSDT last_price 69000.0 > 68000.0', 'sent')
        """,
        (rule_id, "2026-05-05T00:00:01Z"),
    )
    event_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    connection.execute(
        """
        INSERT INTO mobile_alert_delivery (
            mobile_alert_event_id,
            push_device_id,
            channel,
            status,
            attempt_count,
            provider_message_id,
            created_at_utc,
            updated_at_utc
        )
        VALUES (?, ?, 'getui', 'sent', 1, 'task-1', ?, ?)
        """,
        (event_id, push_device_id, "2026-05-05T00:00:02Z", "2026-05-05T00:00:02Z"),
    )
    connection.execute(
        """
        INSERT INTO device_checkpoint (
            push_device_id,
            last_seen_mobile_alert_event_id,
            last_ack_mobile_alert_event_id,
            updated_at_utc
        )
        VALUES (?, ?, ?, ?)
        """,
        (push_device_id, event_id, event_id, "2026-05-05T00:00:03Z"),
    )
    _open_device_session(
        connection,
        session_id="debug-session-1",
        push_device_id=push_device_id,
        transport="sse",
        now_utc="2026-05-05T00:00:04Z",
    )


def _insert_push_device(connection: sqlite3.Connection) -> int:
    connection.execute(
        """
        INSERT INTO push_device (
            push_token,
            platform,
            device_label,
            enabled,
            created_at_utc,
            updated_at_utc
        )
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (
            "ExponentPushToken[test-token]",
            "android",
            "OnePlus 13T",
            "2026-05-05T00:00:00Z",
            "2026-05-05T00:00:00Z",
        ),
    )
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])


def _ms(value: str) -> int:
    normalized = value.replace("Z", "+00:00")
    return int(datetime.fromisoformat(normalized).astimezone(UTC).timestamp() * 1000)


class _FakeSocket:
    def __init__(self, request: bytes) -> None:
        self._request = io.BytesIO(request)
        self.response = io.BytesIO()

    def makefile(self, mode: str, buffering: int | None = None):
        if "r" in mode:
            return self._request
        return self.response

    def sendall(self, data: bytes) -> None:
        self.response.write(data)


def _request_api(
    db_path: Path,
    method: str,
    path: str,
    payload: dict[str, object],
    *,
    read_only_canary: bool = False,
) -> tuple[int, bytes]:
    body = json.dumps(payload).encode("utf-8")
    request = (
        f"{method} {path} HTTP/1.1\r\n"
        "Host: testserver\r\n"
        "Content-Type: application/json\r\n"
        "Connection: close\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
    ).encode("utf-8") + body
    socket = _FakeSocket(request)
    _make_handler(db_path, read_only_canary=read_only_canary)(
        socket,
        ("127.0.0.1", 0),
        object(),
    )
    raw_response = socket.response.getvalue()
    header, _, response_body = raw_response.partition(b"\r\n\r\n")
    status = int(header.split(b" ", 2)[1])
    return status, response_body


if __name__ == "__main__":
    unittest.main()
