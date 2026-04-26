from __future__ import annotations

import operator
import sqlite3
from dataclasses import dataclass
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

OPERATORS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
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
