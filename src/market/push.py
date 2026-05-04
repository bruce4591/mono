from __future__ import annotations

import json
import sqlite3
import urllib.request
from dataclasses import dataclass
from typing import Callable

from market.alerts import evaluate_mobile_alert_rules


EXPO_PUSH_ENDPOINT = "https://exp.host/--/api/v2/push/send"


PushMessage = dict[str, object]
PushSender = Callable[[PushMessage], "PushDeliveryResult"]


@dataclass(frozen=True)
class PushDeliveryResult:
    delivery_status: str
    response_id: str | None = None
    error: str | None = None


def build_expo_push_payload(
    token: str,
    title: str,
    body: str,
    *,
    data: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "to": token,
        "title": title,
        "body": body,
        "sound": "default",
        "channelId": "market-alerts",
        "priority": "high",
    }
    if data:
        payload["data"] = data
    return payload


def send_expo_push_message(message: PushMessage) -> PushDeliveryResult:
    return send_expo_push_payload(
        build_expo_push_payload(
            str(message["push_token"]),
            str(message["title"]),
            str(message["body"]),
            data=message.get("data") if isinstance(message.get("data"), dict) else None,
        )
    )


def send_expo_push_payload(
    payload: dict[str, object],
    *,
    endpoint: str = EXPO_PUSH_ENDPOINT,
    timeout: int = 15,
    opener=urllib.request.urlopen,
) -> PushDeliveryResult:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        return PushDeliveryResult(delivery_status="failed", error=str(error))

    data = response_payload.get("data") if isinstance(response_payload, dict) else None
    if not isinstance(data, dict):
        return PushDeliveryResult(delivery_status="failed", error="invalid Expo response")
    if data.get("status") == "ok":
        return PushDeliveryResult(
            delivery_status="sent",
            response_id=str(data["id"]) if data.get("id") is not None else None,
        )
    return PushDeliveryResult(
        delivery_status="failed",
        error=str(data.get("message") or data.get("details") or response_payload),
    )


def deliver_mobile_alert_pushes(
    connection: sqlite3.Connection,
    *,
    now_utc: str,
    sender: PushSender = send_expo_push_message,
) -> list[PushDeliveryResult]:
    messages = evaluate_mobile_alert_rules(connection, now_utc)
    results: list[PushDeliveryResult] = []
    for message in messages:
        token = str(message["push_token"])
        if not _is_expo_push_token(token):
            result = PushDeliveryResult(
                delivery_status="skipped_invalid_token",
                error="invalid Expo push token",
            )
        else:
            result = sender(message)
        _update_mobile_alert_event_status(
            connection,
            event_id=int(message["mobile_alert_event_id"]),
            status=result.delivery_status,
        )
        results.append(result)
    return results


def _is_expo_push_token(token: str) -> bool:
    return token.startswith("ExponentPushToken[") and token.endswith("]")


def _update_mobile_alert_event_status(
    connection: sqlite3.Connection,
    *,
    event_id: int,
    status: str,
) -> None:
    connection.execute(
        """
        UPDATE mobile_alert_event
        SET delivery_status = ?
        WHERE mobile_alert_event_id = ?
        """,
        (status, event_id),
    )
