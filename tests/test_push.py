from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from market.db import connect, init_database
from market.models import Instrument, MarketSnapshot
from market.push import (
    GetuiCredentials,
    PushDeliveryResult,
    build_expo_push_payload,
    build_getui_auth_payload,
    build_getui_push_payload,
    deliver_mobile_alert_pushes,
    send_auto_push_message,
    send_expo_push_payload,
    send_getui_push_message,
)
from market.repositories import InstrumentRepository, MarketSnapshotRepository


class PushTests(unittest.TestCase):
    def test_build_expo_push_payload_contains_required_fields(self):
        payload = build_expo_push_payload(
            "ExponentPushToken[test-token]",
            "BTC 突破 68000",
            "涨幅 +2.1%，点击查看",
        )

        self.assertEqual(payload["to"], "ExponentPushToken[test-token]")
        self.assertEqual(payload["title"], "BTC 突破 68000")
        self.assertEqual(payload["body"], "涨幅 +2.1%，点击查看")
        self.assertEqual(payload["sound"], "default")
        self.assertEqual(payload["channelId"], "market-alerts")

    def test_deliver_mobile_alert_pushes_marks_successful_event_sent(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                _insert_mobile_push_fixture(connection, push_token="ExponentPushToken[test-token]")
                deliveries = deliver_mobile_alert_pushes(
                    connection,
                    now_utc="2026-05-04T03:00:00Z",
                    sender=lambda message: PushDeliveryResult(
                        delivery_status="sent",
                        response_id="receipt-1",
                    ),
                )
                rows = connection.execute(
                    """
                    SELECT delivery_status
                    FROM mobile_alert_event
                    ORDER BY mobile_alert_event_id
                    """
                ).fetchall()

        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0].delivery_status, "sent")
        self.assertEqual(rows[0]["delivery_status"], "sent")

    def test_deliver_mobile_alert_pushes_skips_invalid_tokens(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            with connect(db_path) as connection:
                _insert_mobile_push_fixture(
                    connection,
                    push_token="invalid-token",
                    getui_cid=None,
                )
                deliveries = deliver_mobile_alert_pushes(
                    connection,
                    now_utc="2026-05-04T03:00:00Z",
                    sender=lambda message: calls.append(message) or PushDeliveryResult(
                        delivery_status="sent"
                    ),
                )
                status = connection.execute(
                    "SELECT delivery_status FROM mobile_alert_event"
                ).fetchone()["delivery_status"]

        self.assertEqual(calls, [])
        self.assertEqual(deliveries[0].delivery_status, "skipped_invalid_token")
        self.assertEqual(status, "skipped_invalid_token")

    def test_deliver_mobile_alert_pushes_sends_getui_only_device(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            with connect(db_path) as connection:
                _insert_mobile_push_fixture(connection, push_token="getui:getui-cid-1")
                deliveries = deliver_mobile_alert_pushes(
                    connection,
                    now_utc="2026-05-04T03:00:00Z",
                    sender=lambda message: calls.append(message) or PushDeliveryResult(
                        delivery_status="sent",
                        response_id="getui-task-1",
                    ),
                )
                status = connection.execute(
                    "SELECT delivery_status FROM mobile_alert_event"
                ).fetchone()["delivery_status"]

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["getui_cid"], "getui-cid-1")
        self.assertEqual(deliveries[0].delivery_status, "sent")
        self.assertEqual(status, "sent")

    def test_deliver_mobile_alert_pushes_skips_disabled_devices(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                _insert_mobile_push_fixture(
                    connection,
                    push_token="ExponentPushToken[test-token]",
                    push_enabled=False,
                )
                deliveries = deliver_mobile_alert_pushes(
                    connection,
                    now_utc="2026-05-04T03:00:00Z",
                    sender=lambda message: PushDeliveryResult(delivery_status="sent"),
                )
                count = connection.execute(
                    "SELECT COUNT(*) FROM mobile_alert_event"
                ).fetchone()[0]

        self.assertEqual(deliveries, [])
        self.assertEqual(count, 0)

    def test_send_expo_push_payload_returns_failed_result_on_http_error(self):
        def failing_opener(request, timeout):
            raise OSError("network down")

        result = send_expo_push_payload(
            {
                "to": "ExponentPushToken[test-token]",
                "title": "test",
                "body": "body",
                "sound": "default",
            },
            opener=failing_opener,
        )

        self.assertEqual(result.delivery_status, "failed")
        self.assertIn("network down", result.error or "")

    def test_build_getui_auth_payload_hashes_secret(self):
        payload = build_getui_auth_payload(
            GetuiCredentials(
                app_id="app-id",
                app_key="app-key",
                master_secret="master-secret",
            ),
            timestamp_ms=1700000000000,
        )

        self.assertEqual(payload["appkey"], "app-key")
        self.assertEqual(payload["timestamp"], "1700000000000")
        self.assertEqual(
            payload["sign"],
            "f8d30b77d13959f8d4f3dd425ef37e04fdffd6a9d0797ab44c083773ba770e40",
        )

    def test_build_getui_push_payload_targets_cid(self):
        payload = build_getui_push_payload(
            "getui-cid-1",
            "BTCUSDT 价格突破",
            "BTCUSDT last_price 69000 > 68000",
            request_id="market0000000001",
            click_url="http://150.109.22.77:8000/instrument.html?market=CRYPTO&symbol=BTCUSDT",
        )

        self.assertEqual(payload["request_id"], "market0000000001")
        self.assertEqual(payload["audience"]["cid"], ["getui-cid-1"])
        self.assertEqual(payload["push_message"]["notification"]["title"], "BTCUSDT 价格突破")
        self.assertEqual(payload["push_message"]["notification"]["click_type"], "url")

    def test_send_auto_push_message_prefers_getui_when_configured(self):
        calls = []

        with patch.dict(
            "os.environ",
            {
                "GETUI_APP_ID": "app-id",
                "GETUI_APP_KEY": "app-key",
                "GETUI_MASTER_SECRET": "master-secret",
            },
        ):
            result = send_auto_push_message(
                {
                    "mobile_alert_event_id": 7,
                    "push_token": "ExponentPushToken[test-token]",
                    "getui_cid": "getui-cid-1",
                    "title": "test",
                    "body": "body",
                    "data": {"url": "/status.html"},
                },
                getui_sender=lambda message: calls.append(message) or PushDeliveryResult(
                    delivery_status="sent",
                    response_id="getui-task-1",
                ),
                expo_sender=lambda message: PushDeliveryResult(delivery_status="failed"),
            )

        self.assertEqual(result.delivery_status, "sent")
        self.assertEqual(result.response_id, "getui-task-1")
        self.assertEqual(calls[0]["getui_cid"], "getui-cid-1")

    def test_send_getui_push_message_returns_failed_on_api_error(self):
        responses = [
            _FakeResponse({"code": 0, "data": {"token": "auth-token"}}),
            _FakeResponse({"code": 1001, "msg": "cid offline"}),
        ]

        def opener(request, timeout):
            return responses.pop(0)

        result = send_getui_push_message(
            {
                "mobile_alert_event_id": 1,
                "getui_cid": "getui-cid-1",
                "title": "test",
                "body": "body",
            },
            credentials=GetuiCredentials(
                app_id="app-id",
                app_key="app-key",
                master_secret="master-secret",
            ),
            opener=opener,
            timestamp_ms=lambda: 1700000000000,
        )

        self.assertEqual(result.delivery_status, "failed")
        self.assertIn("cid offline", result.error or "")


def _insert_mobile_push_fixture(
    connection: sqlite3.Connection,
    *,
    push_token: str,
    getui_cid: str | None = "getui-cid-1",
    push_enabled: bool = True,
) -> int:
    instrument_id = InstrumentRepository(connection).upsert(
        Instrument(
            market="CRYPTO",
            symbol="BTCUSDT",
            display_name="BTC/USDT",
            exchange="BINANCE",
            instrument_type="crypto",
            quote_currency="USDT",
            timezone="UTC",
        )
    )
    MarketSnapshotRepository(connection).upsert(
        MarketSnapshot(
            instrument_id=instrument_id,
            snapshot_ts_utc="2026-05-04T02:59:00Z",
            trade_date_local="2026-05-04",
            last_price=69000.0,
            change_pct=2.0,
            volume_raw=120.0,
            turnover_raw=7_800_000.0,
            quote_currency="USDT",
            source="test",
        )
    )
    connection.execute(
        """
        INSERT INTO push_device (
            push_token,
            getui_cid,
            platform,
            device_label,
            enabled,
            created_at_utc,
            updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            push_token,
            getui_cid,
            "android",
            "OnePlus 13T",
            int(push_enabled),
            "2026-05-04T02:58:00Z",
            "2026-05-04T02:58:00Z",
        ),
    )
    push_device_id = connection.execute(
        "SELECT push_device_id FROM push_device WHERE push_token = ?",
        (push_token,),
    ).fetchone()["push_device_id"]
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
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            push_device_id,
            "BTCUSDT",
            "CRYPTO",
            "price_above",
            68000.0,
            900,
            "2026-05-04T02:58:00Z",
            "2026-05-04T02:58:00Z",
        ),
    )
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])


class _FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def read(self) -> bytes:
        import json

        return json.dumps(self.payload).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
