from __future__ import annotations

import json
import os
import time
import sqlite3
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable
from urllib.parse import quote

from market.alerts import evaluate_mobile_alert_rules


EXPO_PUSH_ENDPOINT = "https://exp.host/--/api/v2/push/send"
GETUI_BASE_ENDPOINT = "https://restapi.getui.com/v2"


PushMessage = dict[str, object]
PushSender = Callable[[PushMessage], "PushDeliveryResult"]


@dataclass(frozen=True)
class PushDeliveryResult:
    delivery_status: str
    response_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class GetuiCredentials:
    app_id: str
    app_key: str
    master_secret: str

    @classmethod
    def from_env(cls) -> "GetuiCredentials | None":
        app_id = os.environ.get("GETUI_APP_ID") or os.environ.get("GETUI_APPID")
        app_key = os.environ.get("GETUI_APP_KEY")
        master_secret = os.environ.get("GETUI_MASTER_SECRET")
        if not app_id or not app_key or not master_secret:
            return None
        return cls(app_id=app_id, app_key=app_key, master_secret=master_secret)


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


def build_getui_auth_payload(
    credentials: GetuiCredentials,
    *,
    timestamp_ms: int,
) -> dict[str, str]:
    timestamp = str(timestamp_ms)
    sign_source = f"{credentials.app_key}{timestamp}{credentials.master_secret}"
    return {
        "sign": sha256(sign_source.encode("utf-8")).hexdigest(),
        "timestamp": timestamp,
        "appkey": credentials.app_key,
    }


def build_getui_push_payload(
    cid: str,
    title: str,
    body: str,
    *,
    request_id: str,
    click_url: str | None = None,
) -> dict[str, object]:
    notification: dict[str, object] = {
        "title": title,
        "body": body,
        "channel_id": "market-alerts",
        "channel_name": "Market Alerts",
        "channel_level": 4,
    }
    if click_url:
        notification["click_type"] = "url"
        notification["url"] = click_url
    else:
        notification["click_type"] = "none"
    return {
        "request_id": request_id,
        "settings": {
            "ttl": 7200000,
            "strategy": {"default": 1},
        },
        "audience": {"cid": [cid]},
        "push_message": {"notification": notification},
        "push_channel": {
            "android": {
                "ups": {
                    "notification": notification,
                },
            },
        },
    }


def send_getui_push_message(
    message: PushMessage,
    *,
    credentials: GetuiCredentials | None = None,
    endpoint: str = GETUI_BASE_ENDPOINT,
    timeout: int = 15,
    opener=urllib.request.urlopen,
    timestamp_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> PushDeliveryResult:
    resolved_credentials = credentials or GetuiCredentials.from_env()
    if resolved_credentials is None:
        return PushDeliveryResult(
            delivery_status="skipped_getui_not_configured",
            error="GETUI_APP_ID, GETUI_APP_KEY, or GETUI_MASTER_SECRET is missing",
        )
    cid = str(message.get("getui_cid") or "")
    if not cid:
        return PushDeliveryResult(
            delivery_status="skipped_getui_missing_cid",
            error="missing Getui CID",
        )
    try:
        token_payload = _post_json(
            f"{endpoint}/{quote(resolved_credentials.app_id)}/auth",
            build_getui_auth_payload(
                resolved_credentials,
                timestamp_ms=timestamp_ms(),
            ),
            opener=opener,
            timeout=timeout,
        )
        if int(token_payload.get("code", -1)) != 0:
            return PushDeliveryResult(
                delivery_status="failed",
                error=str(token_payload.get("msg") or token_payload),
            )
        data = token_payload.get("data")
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            return PushDeliveryResult(
                delivery_status="failed",
                error="invalid Getui auth response",
            )

        event_id = int(message.get("mobile_alert_event_id") or 0)
        click_url = _getui_click_url(message)
        push_payload = _post_json(
            f"{endpoint}/{quote(resolved_credentials.app_id)}/push/single/cid",
            build_getui_push_payload(
                cid,
                str(message["title"]),
                str(message["body"]),
                request_id=f"market{event_id:010d}",
                click_url=click_url,
            ),
            headers={"token": token},
            opener=opener,
            timeout=timeout,
        )
    except Exception as error:
        return PushDeliveryResult(delivery_status="failed", error=str(error))

    if int(push_payload.get("code", -1)) != 0:
        return PushDeliveryResult(
            delivery_status="failed",
            error=str(push_payload.get("msg") or push_payload),
        )
    data = push_payload.get("data")
    task_id = None
    if isinstance(data, dict):
        task_id = data.get(cid) or data.get("taskid") or data.get("task_id")
    return PushDeliveryResult(
        delivery_status="sent",
        response_id=str(task_id) if task_id is not None else None,
    )


def send_auto_push_message(
    message: PushMessage,
    *,
    getui_sender: PushSender = send_getui_push_message,
    expo_sender: PushSender = send_expo_push_message,
) -> PushDeliveryResult:
    getui_cid = message.get("getui_cid")
    if isinstance(getui_cid, str) and getui_cid.strip() and GetuiCredentials.from_env():
        return getui_sender(message)
    return expo_sender(message)


def deliver_mobile_alert_pushes(
    connection: sqlite3.Connection,
    *,
    now_utc: str,
    sender: PushSender = send_auto_push_message,
) -> list[PushDeliveryResult]:
    messages = evaluate_mobile_alert_rules(connection, now_utc)
    results: list[PushDeliveryResult] = []
    for message in messages:
        token = str(message["push_token"])
        getui_cid = message.get("getui_cid")
        has_getui_cid = isinstance(getui_cid, str) and bool(getui_cid.strip())
        if not has_getui_cid and not _is_expo_push_token(token):
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


def _post_json(
    endpoint: str,
    payload: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
    timeout: int,
    opener,
) -> dict[str, object]:
    request_headers = {"Content-Type": "application/json;charset=utf-8"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with opener(request, timeout=timeout) as response:
        response_payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(response_payload, dict):
        raise ValueError("invalid JSON response")
    return response_payload


def _getui_click_url(message: PushMessage) -> str | None:
    data = message.get("data")
    if not isinstance(data, dict):
        return None
    url = data.get("url")
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url
    base_url = os.environ.get("MARKET_PUBLIC_BASE_URL", "").rstrip("/")
    if not base_url or not url.startswith("/"):
        return None
    return f"{base_url}{url}"


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
