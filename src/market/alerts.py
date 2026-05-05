from __future__ import annotations

import operator
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from market.models import AlertEvent, AlertRule
from market.repositories import AlertEventRepository, AlertRuleRepository

SnapshotPayload = dict[str, object]
MetricResolver = Callable[[SnapshotPayload], float | None]


@dataclass(frozen=True)
class AlertEvaluationResult:
    rules_checked: int
    events_created: int


DEFAULT_METRIC_RESOLVERS: dict[str, MetricResolver] = {
    "change_pct": lambda snapshot: _optional_float(snapshot.get("change_pct")),
    "turnover_raw": lambda snapshot: _optional_float(snapshot.get("turnover_raw")),
    "volume_raw": lambda snapshot: _optional_float(snapshot.get("volume_raw")),
    "last_price": lambda snapshot: _optional_float(snapshot.get("last_price")),
}

ALERT_METRICS: tuple[dict[str, str], ...] = (
    {
        "key": "last_price",
        "label": "最新价",
        "description": "来自最新 market_snapshot.last_price，适合价格突破提醒。",
    },
    {
        "key": "change_pct",
        "label": "涨跌幅",
        "description": "来自最新 market_snapshot.change_pct，单位为百分比。",
    },
    {
        "key": "volume_raw",
        "label": "成交量",
        "description": "来自最新 market_snapshot.volume_raw，保留数据源原始单位。",
    },
    {
        "key": "turnover_raw",
        "label": "成交额",
        "description": "来自最新 market_snapshot.turnover_raw，保留原始币种。",
    },
)

CHART_INDICATORS: tuple[str, ...] = ("MA", "VOL", "MACD")
MOBILE_ALERT_COALESCE_SECONDS = 60

OPERATORS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
}

MOBILE_ALERT_CONDITIONS: dict[str, tuple[str, str, Callable[[float, float], bool], str]] = {
    "price_above": ("last_price", ">", operator.gt, "价格提醒"),
    "price_below": ("last_price", "<", operator.lt, "价格提醒"),
    "change_pct_above": ("change_pct", ">", operator.gt, "涨跌幅提醒"),
    "change_pct_below": ("change_pct", "<", operator.lt, "涨跌幅提醒"),
}


def evaluate_alert_rules(
    connection: sqlite3.Connection,
    *,
    triggered_at_utc: str,
    metric_resolvers: dict[str, MetricResolver] | None = None,
) -> AlertEvaluationResult:
    resolvers = {**DEFAULT_METRIC_RESOLVERS, **(metric_resolvers or {})}
    rules = AlertRuleRepository(connection).list_active()
    events = AlertEventRepository(connection)
    created = 0
    for rule in rules:
        snapshot = _latest_snapshot_for_rule(connection, rule)
        if snapshot is None:
            continue
        resolver = resolvers.get(rule.metric)
        if resolver is None:
            continue
        observed_value = resolver(snapshot)
        if observed_value is None:
            continue
        comparator = OPERATORS.get(rule.operator)
        if comparator is None:
            continue
        if comparator(observed_value, rule.threshold):
            rule_id = rule.rule_id
            instrument_id = int(snapshot["instrument_id"])
            if rule_id is None:
                continue
            events.insert(
                AlertEvent(
                    rule_id=rule_id,
                    rule_name=rule.name,
                    instrument_id=instrument_id,
                    market=rule.market,
                    symbol=rule.symbol,
                    triggered_at_utc=triggered_at_utc,
                    metric=rule.metric,
                    observed_value=observed_value,
                    threshold=rule.threshold,
                    message=_format_alert_message(rule, observed_value),
                )
            )
            created += 1
    return AlertEvaluationResult(rules_checked=len(rules), events_created=created)


def evaluate_mobile_alert_rules(
    connection: sqlite3.Connection,
    now_utc: str,
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            mobile_alert_rule.mobile_alert_rule_id,
            mobile_alert_rule.push_device_id,
            mobile_alert_rule.symbol,
            mobile_alert_rule.market,
            mobile_alert_rule.condition_type,
            mobile_alert_rule.source_type,
            mobile_alert_rule.metric_key,
            mobile_alert_rule.operator,
            mobile_alert_rule.indicator_id,
            mobile_alert_rule.threshold,
            mobile_alert_rule.cooldown_seconds,
            push_device.push_token,
            push_device.getui_cid
        FROM mobile_alert_rule
        JOIN push_device
            ON push_device.push_device_id = mobile_alert_rule.push_device_id
        WHERE mobile_alert_rule.enabled = 1
            AND push_device.enabled = 1
        ORDER BY mobile_alert_rule.mobile_alert_rule_id
        """
    ).fetchall()
    messages: list[dict[str, object]] = []
    for row in rows:
        condition = _mobile_alert_condition_for_row(row)
        if condition is None:
            continue
        metric, operator_label, comparator, title_suffix = condition
        if str(row["source_type"]) == "custom_indicator":
            snapshot = _latest_mobile_indicator_value(
                connection,
                market=str(row["market"]),
                symbol=str(row["symbol"]),
                indicator_id=int(row["indicator_id"]),
            )
        else:
            snapshot = _latest_mobile_snapshot(
                connection,
                market=str(row["market"]),
                symbol=str(row["symbol"]),
            )
        if snapshot is None:
            continue
        observed_value = _optional_float(snapshot.get(metric))
        if observed_value is None:
            continue
        threshold = float(row["threshold"])
        if not comparator(observed_value, threshold):
            continue
        rule_id = int(row["mobile_alert_rule_id"])
        if _mobile_alert_in_cooldown(
            connection,
            rule_id=rule_id,
            now_utc=now_utc,
            cooldown_seconds=int(row["cooldown_seconds"]),
        ):
            continue
        message = (
            f"{row['symbol']} {metric} "
            f"{_format_float(observed_value)} {operator_label} {_format_float(threshold)}"
        )
        event_id = _coalesce_recent_mobile_alert_event(
            connection,
            push_device_id=int(row["push_device_id"]),
            market=str(row["market"]),
            symbol=str(row["symbol"]),
            source_type=str(row["source_type"]),
            metric=metric,
            indicator_id=(
                int(row["indicator_id"]) if row["indicator_id"] is not None else None
            ),
            now_utc=now_utc,
            observed_value=observed_value,
            message=message,
        )
        if event_id is None:
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
                (rule_id, now_utc, observed_value, message, "pending"),
            )
            event_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        messages.append(
            {
                "mobile_alert_event_id": event_id,
                "mobile_alert_rule_id": rule_id,
                "push_device_id": int(row["push_device_id"]),
                "push_token": str(row["push_token"]),
                "getui_cid": (
                    str(row["getui_cid"]) if row["getui_cid"] is not None else None
                ),
                "title": f"{row['symbol']} {title_suffix}",
                "body": message,
                "sound": "default",
                "channelId": "market-alerts",
                "data": {
                    "market": str(row["market"]),
                    "symbol": str(row["symbol"]),
                    "url": (
                        "/instrument.html?"
                        f"market={row['market']}&symbol={row['symbol']}"
                    ),
                },
            }
        )
    return messages


def _latest_snapshot_for_rule(
    connection: sqlite3.Connection,
    rule: AlertRule,
) -> SnapshotPayload | None:
    row = connection.execute(
        """
        SELECT
            instrument.instrument_id,
            instrument.market,
            instrument.symbol,
            market_snapshot.snapshot_ts_utc,
            market_snapshot.trade_date_local,
            market_snapshot.last_price,
            market_snapshot.change_pct,
            market_snapshot.volume_raw,
            market_snapshot.turnover_raw,
            market_snapshot.quote_currency,
            market_snapshot.source
        FROM instrument
        JOIN market_snapshot
            ON market_snapshot.instrument_id = instrument.instrument_id
        WHERE instrument.market = ?
            AND instrument.symbol = ?
            AND instrument.is_active = 1
        ORDER BY market_snapshot.snapshot_ts_utc DESC
        LIMIT 1
        """,
        (rule.market, rule.symbol),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def _latest_mobile_snapshot(
    connection: sqlite3.Connection,
    *,
    market: str,
    symbol: str,
) -> SnapshotPayload | None:
    row = connection.execute(
        """
        SELECT
            instrument.instrument_id,
            instrument.market,
            instrument.symbol,
            market_snapshot.snapshot_ts_utc,
            market_snapshot.last_price,
            market_snapshot.change_pct
        FROM instrument
        JOIN market_snapshot
            ON market_snapshot.instrument_id = instrument.instrument_id
        WHERE instrument.market = ?
            AND instrument.symbol = ?
            AND instrument.is_active = 1
        ORDER BY market_snapshot.snapshot_ts_utc DESC
        LIMIT 1
        """,
        (market, symbol),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def _latest_mobile_indicator_value(
    connection: sqlite3.Connection,
    *,
    market: str,
    symbol: str,
    indicator_id: int,
) -> SnapshotPayload | None:
    row = connection.execute(
        """
        SELECT
            instrument.instrument_id,
            instrument.market,
            instrument.symbol,
            indicator_value.value_ts_utc AS snapshot_ts_utc,
            indicator_value.value AS indicator_value
        FROM instrument
        JOIN indicator_value
            ON indicator_value.instrument_id = instrument.instrument_id
        WHERE instrument.market = ?
            AND instrument.symbol = ?
            AND indicator_value.indicator_id = ?
            AND instrument.is_active = 1
            AND indicator_value.status = 'ok'
        ORDER BY indicator_value.value_ts_utc DESC, indicator_value.indicator_value_id DESC
        LIMIT 1
        """,
        (market, symbol, indicator_id),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def _mobile_alert_condition_for_row(
    row: sqlite3.Row,
) -> tuple[str, str, Callable[[float, float], bool], str] | None:
    if str(row["source_type"]) == "custom_indicator":
        operator_label = str(row["operator"] or "")
        comparator = OPERATORS.get(operator_label)
        if comparator is None or row["indicator_id"] is None:
            return None
        return ("indicator_value", operator_label, comparator, "自定义指标提醒")
    return MOBILE_ALERT_CONDITIONS.get(str(row["condition_type"]))


def _mobile_alert_in_cooldown(
    connection: sqlite3.Connection,
    *,
    rule_id: int,
    now_utc: str,
    cooldown_seconds: int,
) -> bool:
    row = connection.execute(
        """
        SELECT triggered_at_utc
        FROM mobile_alert_event
        WHERE mobile_alert_rule_id = ?
        ORDER BY triggered_at_utc DESC, mobile_alert_event_id DESC
        LIMIT 1
        """,
        (rule_id,),
    ).fetchone()
    if row is None:
        return False
    elapsed = _parse_utc(now_utc) - _parse_utc(str(row["triggered_at_utc"]))
    return elapsed.total_seconds() < cooldown_seconds


def _coalesce_recent_mobile_alert_event(
    connection: sqlite3.Connection,
    *,
    push_device_id: int,
    market: str,
    symbol: str,
    source_type: str,
    metric: str,
    indicator_id: int | None,
    now_utc: str,
    observed_value: float,
    message: str,
) -> int | None:
    rows = connection.execute(
        """
        SELECT
            mobile_alert_event.mobile_alert_event_id,
            mobile_alert_event.triggered_at_utc,
            mobile_alert_rule.condition_type,
            mobile_alert_rule.source_type,
            mobile_alert_rule.metric_key,
            mobile_alert_rule.operator,
            mobile_alert_rule.indicator_id
        FROM mobile_alert_event
        JOIN mobile_alert_rule
            ON mobile_alert_rule.mobile_alert_rule_id =
                mobile_alert_event.mobile_alert_rule_id
        WHERE mobile_alert_rule.push_device_id = ?
            AND mobile_alert_rule.market = ?
            AND mobile_alert_rule.symbol = ?
        ORDER BY triggered_at_utc DESC, mobile_alert_event_id DESC
        LIMIT 20
        """,
        (push_device_id, market, symbol),
    ).fetchall()
    event_id = None
    for row in rows:
        elapsed = _parse_utc(now_utc) - _parse_utc(str(row["triggered_at_utc"]))
        elapsed_seconds = elapsed.total_seconds()
        if elapsed_seconds < 0:
            continue
        if elapsed_seconds > MOBILE_ALERT_COALESCE_SECONDS:
            break
        row_metric = _mobile_alert_metric_for_row(row)
        if row_metric != metric:
            continue
        if source_type == "custom_indicator" and row["indicator_id"] != indicator_id:
            continue
        event_id = int(row["mobile_alert_event_id"])
        break
    if event_id is None:
        return None
    connection.execute(
        """
        UPDATE mobile_alert_event
        SET triggered_at_utc = ?,
            observed_value = ?,
            message = ?,
            delivery_status = 'pending'
        WHERE mobile_alert_event_id = ?
        """,
        (now_utc, observed_value, message, event_id),
    )
    return event_id


def _mobile_alert_metric_for_row(row: sqlite3.Row) -> str | None:
    if str(row["source_type"]) == "custom_indicator":
        return "indicator_value"
    condition = MOBILE_ALERT_CONDITIONS.get(str(row["condition_type"]))
    if condition is None:
        return None
    return condition[0]


def _format_alert_message(rule: AlertRule, observed_value: float) -> str:
    return (
        f"{rule.symbol} {rule.metric} "
        f"{_format_float(observed_value)} {rule.operator} {_format_float(rule.threshold)}"
    )


def _format_float(value: float) -> str:
    return str(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
