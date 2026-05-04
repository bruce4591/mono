from __future__ import annotations

import sqlite3


def enqueue_mobile_alert_delivery(
    connection: sqlite3.Connection,
    *,
    event_id: int,
    push_device_id: int,
    channel: str,
    now_utc: str,
) -> int:
    connection.execute(
        """
        INSERT INTO mobile_alert_delivery (
            mobile_alert_event_id,
            push_device_id,
            channel,
            status,
            attempt_count,
            created_at_utc,
            updated_at_utc
        )
        VALUES (?, ?, ?, 'pending', 0, ?, ?)
        ON CONFLICT(mobile_alert_event_id, push_device_id, channel) DO UPDATE SET
            updated_at_utc = excluded.updated_at_utc
        """,
        (event_id, push_device_id, channel, now_utc, now_utc),
    )
    row = connection.execute(
        """
        SELECT mobile_alert_delivery_id
        FROM mobile_alert_delivery
        WHERE mobile_alert_event_id = ?
            AND push_device_id = ?
            AND channel = ?
        """,
        (event_id, push_device_id, channel),
    ).fetchone()
    if row is None:
        raise RuntimeError("mobile alert delivery enqueue did not return a row")
    return int(row["mobile_alert_delivery_id"])


def mark_mobile_alert_delivery(
    connection: sqlite3.Connection,
    *,
    delivery_id: int,
    status: str,
    provider_message_id: str | None,
    error: str | None,
    now_utc: str,
) -> None:
    connection.execute(
        """
        UPDATE mobile_alert_delivery
        SET status = ?,
            attempt_count = attempt_count + 1,
            provider_message_id = ?,
            last_error = ?,
            updated_at_utc = ?
        WHERE mobile_alert_delivery_id = ?
        """,
        (status, provider_message_id, error, now_utc, delivery_id),
    )


def list_pending_mobile_alert_deliveries(
    connection: sqlite3.Connection,
    *,
    limit: int = 100,
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            mobile_alert_delivery.mobile_alert_delivery_id,
            mobile_alert_delivery.mobile_alert_event_id,
            mobile_alert_delivery.push_device_id,
            mobile_alert_delivery.channel,
            mobile_alert_delivery.status,
            mobile_alert_delivery.attempt_count,
            mobile_alert_delivery.provider_message_id,
            mobile_alert_delivery.last_error,
            mobile_alert_delivery.created_at_utc,
            mobile_alert_delivery.updated_at_utc
        FROM mobile_alert_delivery
        WHERE mobile_alert_delivery.status = 'pending'
        ORDER BY mobile_alert_delivery.created_at_utc, mobile_alert_delivery_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_delivery_payload(row) for row in rows]


def _delivery_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "mobile_alert_delivery_id": int(row["mobile_alert_delivery_id"]),
        "mobile_alert_event_id": int(row["mobile_alert_event_id"]),
        "push_device_id": int(row["push_device_id"]),
        "channel": str(row["channel"]),
        "status": str(row["status"]),
        "attempt_count": int(row["attempt_count"]),
        "provider_message_id": (
            str(row["provider_message_id"])
            if row["provider_message_id"] is not None
            else None
        ),
        "last_error": str(row["last_error"]) if row["last_error"] is not None else None,
        "created_at_utc": str(row["created_at_utc"]),
        "updated_at_utc": str(row["updated_at_utc"]),
    }
