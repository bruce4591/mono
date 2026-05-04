from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from market.db import connect, init_database
from market.mobile_delivery import (
    enqueue_mobile_alert_delivery,
    list_pending_mobile_alert_deliveries,
    mark_mobile_alert_delivery,
)


class MobileDeliveryTests(unittest.TestCase):
    def test_enqueue_and_mark_mobile_alert_delivery(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                push_device_id = _insert_push_device(connection)
                event_id = _insert_mobile_alert_event(connection, push_device_id)
                delivery_id = enqueue_mobile_alert_delivery(
                    connection,
                    event_id=event_id,
                    push_device_id=push_device_id,
                    channel="getui",
                    now_utc="2026-05-05T00:00:00Z",
                )

                pending = list_pending_mobile_alert_deliveries(connection)
                mark_mobile_alert_delivery(
                    connection,
                    delivery_id=delivery_id,
                    status="sent",
                    provider_message_id="provider-1",
                    error=None,
                    now_utc="2026-05-05T00:00:01Z",
                )
                row = connection.execute(
                    """
                    SELECT status, attempt_count, provider_message_id, last_error
                    FROM mobile_alert_delivery
                    WHERE mobile_alert_delivery_id = ?
                    """,
                    (delivery_id,),
                ).fetchone()

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["channel"], "getui")
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["attempt_count"], 1)
        self.assertEqual(row["provider_message_id"], "provider-1")
        self.assertIsNone(row["last_error"])

    def test_enqueue_mobile_alert_delivery_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                push_device_id = _insert_push_device(connection)
                event_id = _insert_mobile_alert_event(connection, push_device_id)
                first_id = enqueue_mobile_alert_delivery(
                    connection,
                    event_id=event_id,
                    push_device_id=push_device_id,
                    channel="getui",
                    now_utc="2026-05-05T00:00:00Z",
                )
                second_id = enqueue_mobile_alert_delivery(
                    connection,
                    event_id=event_id,
                    push_device_id=push_device_id,
                    channel="getui",
                    now_utc="2026-05-05T00:00:02Z",
                )
                count = connection.execute(
                    "SELECT COUNT(*) FROM mobile_alert_delivery"
                ).fetchone()[0]

        self.assertEqual(first_id, second_id)
        self.assertEqual(count, 1)


def _insert_push_device(connection: sqlite3.Connection) -> int:
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
        VALUES (?, ?, 'android', 'test device', 1, ?, ?)
        """,
        (
            "getui:cid-1",
            "cid-1",
            "2026-05-05T00:00:00Z",
            "2026-05-05T00:00:00Z",
        ),
    )
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])


def _insert_mobile_alert_event(
    connection: sqlite3.Connection,
    push_device_id: int,
) -> int:
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
        VALUES (?, 'BTCUSDT', 'CRYPTO', 'price_above', 68000, 900, 1, ?, ?)
        """,
        (
            push_device_id,
            "2026-05-05T00:00:00Z",
            "2026-05-05T00:00:00Z",
        ),
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
        VALUES (?, ?, 69000, 'BTCUSDT last_price 69000 > 68000', 'pending')
        """,
        (rule_id, "2026-05-05T00:00:00Z"),
    )
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
