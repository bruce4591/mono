from __future__ import annotations

import operator
import sqlite3
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from market.models import AlertEvent, AlertRule
from market.repositories import AlertEventRepository, AlertRuleRepository

SnapshotPayload = dict[str, object]
MetricResolver = Callable[[SnapshotPayload], float | None]


@dataclass(frozen=True)
class AlertEvaluationResult:
    rules_checked: int
    events_created: int


@dataclass(frozen=True)
class MobileTechnicalSignal:
    title_suffix: str
    metric: str
    observed_value: float
    message: str
    dedupe_key: str
    metadata: dict[str, object]


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
CRYPTO_RANKING_ALERT_LIMIT = 20
CRYPTO_RANKING_ALERT_CREATED_BY = "system_crypto_ranking_ma11"
CRYPTO_RANKING_ALERT_BOARDS = (
    "CRYPTO_TURNOVER_TOP50",
    "CRYPTO_FUTURES_TURNOVER_TOP50",
)
CRYPTO_RANKING_ALERT_CONDITIONS = (
    {
        "condition_type": "ma11_breakout_1d",
        "threshold": 0.0,
        "cooldown_seconds": 24 * 60 * 60,
        "metric_key": "ma11_cross",
        "operator": ">",
    },
)

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
    _ensure_mobile_alert_event_dedupe_column(connection)
    _sync_crypto_ranking_mobile_alert_rules(connection, now_utc=now_utc)
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
            push_device.getui_cid,
            mobile_alert_rule.created_at_utc
        FROM mobile_alert_rule
        JOIN push_device
            ON push_device.push_device_id = mobile_alert_rule.push_device_id
        WHERE mobile_alert_rule.enabled = TRUE
            AND push_device.enabled = TRUE
        ORDER BY mobile_alert_rule.mobile_alert_rule_id
        """
    ).fetchall()
    messages: list[dict[str, object]] = []
    for row in rows:
        if str(row["source_type"]) == "technical":
            technical_signal = _mobile_technical_signal_for_row(connection, row)
            if technical_signal is None:
                continue
            if not _technical_signal_is_after_rule_creation(row, technical_signal):
                continue
            rule_id = int(row["mobile_alert_rule_id"])
            if _mobile_alert_in_cooldown(
                connection,
                rule_id=rule_id,
                now_utc=now_utc,
                cooldown_seconds=int(row["cooldown_seconds"]),
            ):
                continue
            event_id = _insert_mobile_alert_event(
                connection,
                rule_id=rule_id,
                now_utc=now_utc,
                observed_value=technical_signal.observed_value,
                message=technical_signal.message,
                dedupe_key=technical_signal.dedupe_key,
                alert_metadata=technical_signal.metadata,
            )
            if event_id is None:
                continue
            messages.append(
                _mobile_push_message_for_event(
                    row,
                    event_id=event_id,
                    title_suffix=technical_signal.title_suffix,
                    body=technical_signal.message,
                    metadata=technical_signal.metadata,
                )
            )
            continue
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
            event_id = _insert_mobile_alert_event(
                connection,
                rule_id=rule_id,
                now_utc=now_utc,
                observed_value=observed_value,
                message=message,
                dedupe_key=None,
                alert_metadata={},
            )
            if event_id is None:
                continue
        messages.append(
            _mobile_push_message_for_event(
                row,
                event_id=event_id,
                title_suffix=title_suffix,
                body=message,
            )
        )
    return messages


def _sync_crypto_ranking_mobile_alert_rules(
    connection: sqlite3.Connection,
    *,
    now_utc: str,
) -> None:
    devices = _active_crypto_ranking_alert_devices(connection)
    if not devices:
        return
    instruments = _latest_crypto_ranking_instruments(connection)
    active_pairs = {(str(row["market"]), str(row["symbol"])) for row in instruments}
    active_device_ids = {int(row["push_device_id"]) for row in devices}
    existing_system_rules = connection.execute(
        """
        SELECT mobile_alert_rule_id, push_device_id, market, symbol, condition_type
        FROM mobile_alert_rule
        WHERE source_type = 'technical'
            AND created_by = ?
        """,
        (CRYPTO_RANKING_ALERT_CREATED_BY,),
    ).fetchall()
    existing_keys = {
        (
            int(row["push_device_id"]),
            str(row["market"]),
            str(row["symbol"]),
            str(row["condition_type"]),
        )
        for row in existing_system_rules
    }
    for row in existing_system_rules:
        should_enable = (
            int(row["push_device_id"]) in active_device_ids
            and (str(row["market"]), str(row["symbol"])) in active_pairs
        )
        connection.execute(
            """
            UPDATE mobile_alert_rule
            SET enabled = ?, updated_at_utc = ?
            WHERE mobile_alert_rule_id = ?
            """,
            (
                should_enable,
                now_utc,
                int(row["mobile_alert_rule_id"]),
            ),
        )
    for device in devices:
        push_device_id = int(device["push_device_id"])
        for instrument in instruments:
            market = str(instrument["market"])
            symbol = str(instrument["symbol"])
            for condition in CRYPTO_RANKING_ALERT_CONDITIONS:
                condition_type = str(condition["condition_type"])
                key = (push_device_id, market, symbol, condition_type)
                if key in existing_keys:
                    continue
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
                    VALUES (?, ?, ?, ?, 'technical', ?, ?, ?, ?, TRUE, ?, ?, ?)
                    """,
                    (
                        push_device_id,
                        symbol,
                        market,
                        condition_type,
                        str(condition["metric_key"]),
                        str(condition["operator"]),
                        float(condition["threshold"]),
                        int(condition["cooldown_seconds"]),
                        CRYPTO_RANKING_ALERT_CREATED_BY,
                        now_utc,
                        now_utc,
                    ),
                )
                existing_keys.add(key)


def _active_crypto_ranking_alert_devices(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT push_device_id
        FROM push_device
        WHERE enabled = TRUE
        ORDER BY
            CASE
                WHEN getui_cid IS NOT NULL AND getui_cid != '' THEN 0
                ELSE 1
            END,
            updated_at_utc DESC,
            push_device_id DESC
        LIMIT 1
        """
    ).fetchall()


def _latest_crypto_ranking_instruments(
    connection: sqlite3.Connection,
) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT DISTINCT
            instrument.market,
            instrument.symbol,
            ranking_snapshot.board_name,
            CASE ranking_snapshot.board_name
                WHEN ? THEN 0
                ELSE 1
            END AS board_priority
        FROM ranking_snapshot
        JOIN instrument
            ON instrument.instrument_id = ranking_snapshot.instrument_id
        WHERE ranking_snapshot.board_name IN (?, ?)
            AND ranking_snapshot.rank <= ?
            AND ranking_snapshot.snapshot_ts_utc = (
                SELECT MAX(latest_ranking.snapshot_ts_utc)
                FROM ranking_snapshot AS latest_ranking
                WHERE latest_ranking.board_name = ranking_snapshot.board_name
            )
            AND instrument.is_active = TRUE
        ORDER BY instrument.symbol, board_priority, instrument.market
        """,
        (
            CRYPTO_RANKING_ALERT_BOARDS[1],
            CRYPTO_RANKING_ALERT_BOARDS[0],
            CRYPTO_RANKING_ALERT_BOARDS[1],
            CRYPTO_RANKING_ALERT_LIMIT,
        ),
    ).fetchall()
    selected: dict[str, dict[str, str]] = {}
    for row in rows:
        symbol = str(row["symbol"])
        if symbol in selected:
            continue
        selected[symbol] = {
            "market": str(row["market"]),
            "symbol": symbol,
        }
    return list(selected.values())


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
            AND instrument.is_active = TRUE
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
            AND instrument.is_active = TRUE
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
            AND instrument.is_active = TRUE
            AND indicator_value.status = 'ok'
        ORDER BY indicator_value.value_ts_utc DESC, indicator_value.indicator_value_id DESC
        LIMIT 1
        """,
        (market, symbol, indicator_id),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def _mobile_technical_signal_for_row(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> MobileTechnicalSignal | None:
    condition_type = str(row["condition_type"])
    market = str(row["market"])
    symbol = str(row["symbol"])
    if condition_type == "ma11_breakout_volume_15m":
        signal = _latest_intraday_ma11_cross(
            connection,
            market=market,
            symbol=symbol,
            interval="15m",
            direction="up",
            volume_multiplier=float(row["threshold"]),
        )
        if signal is None:
            return None
        return MobileTechnicalSignal(
            title_suffix="15m MA11 突破",
            metric="ma11_volume_ratio",
            observed_value=signal["close"],
            message=(
                f"{symbol} 15m MA11 突破 close {_format_float(signal['close'])} "
                f"> MA11 {_format_float(signal['ma11'])}，"
                f"量比 {signal['volume_ratio']:.2f}x，K线 {signal['bar_key']}"
            ),
            dedupe_key=f"{condition_type}:{market}:{symbol}:{signal['bar_key']}",
            metadata={
                "period": "15m",
                "bar_time": signal["bar_key"],
                "price": signal["close"],
                "direction": "up",
                "label": "15m MA11 突破",
                "condition_label": "15m MA11 突破 + 量比 >= 1.5x",
                "ma11": signal["ma11"],
                "volume_ratio": signal["volume_ratio"],
            },
        )
    if condition_type == "ma11_breakdown_15m":
        signal = _latest_intraday_ma11_cross(
            connection,
            market=market,
            symbol=symbol,
            interval="15m",
            direction="down",
            volume_multiplier=None,
        )
        if signal is None:
            return None
        return MobileTechnicalSignal(
            title_suffix="15m MA11 跌破",
            metric="ma11_cross",
            observed_value=signal["close"],
            message=(
                f"{symbol} 15m MA11 跌破 close {_format_float(signal['close'])} "
                f"< MA11 {_format_float(signal['ma11'])}，K线 {signal['bar_key']}"
            ),
            dedupe_key=f"{condition_type}:{market}:{symbol}:{signal['bar_key']}",
            metadata={
                "period": "15m",
                "bar_time": signal["bar_key"],
                "price": signal["close"],
                "direction": "down",
                "label": "15m MA11 跌破",
                "condition_label": "15m MA11 跌破",
                "ma11": signal["ma11"],
            },
        )
    if condition_type == "ma11_breakout_1d":
        signal = _latest_daily_ma11_cross(
            connection,
            market=market,
            symbol=symbol,
            direction="up",
        )
        if signal is None:
            return None
        return MobileTechnicalSignal(
            title_suffix="1d MA11 突破",
            metric="ma11_cross",
            observed_value=signal["close"],
            message=(
                f"{symbol} 1d MA11 突破 close {_format_float(signal['close'])} "
                f"> MA11 {_format_float(signal['ma11'])}，交易日 {signal['bar_key']}"
            ),
            dedupe_key=f"{condition_type}:{market}:{symbol}:{signal['bar_key']}",
            metadata={
                "period": "1d",
                "bar_time": signal["bar_key"],
                "price": signal["close"],
                "direction": "up",
                "label": "1d MA11 突破",
                "condition_label": "1d MA11 突破",
                "ma11": signal["ma11"],
            },
        )
    return None


def _technical_signal_is_after_rule_creation(
    row: sqlite3.Row,
    signal: MobileTechnicalSignal,
) -> bool:
    created_at = _parse_utc(str(row["created_at_utc"]))
    signal_time = _parse_technical_signal_time(signal.metadata)
    if signal_time is None:
        return True
    return signal_time > created_at


def _parse_technical_signal_time(metadata: dict[str, object]) -> datetime | None:
    period = metadata.get("period")
    bar_time = metadata.get("bar_time")
    if not isinstance(bar_time, str) or not bar_time:
        return None
    if period == "1d" and "T" not in bar_time:
        return datetime.fromisoformat(bar_time).replace(tzinfo=UTC) + timedelta(days=1)
    return _parse_utc(bar_time)


def _latest_intraday_ma11_cross(
    connection: sqlite3.Connection,
    *,
    market: str,
    symbol: str,
    interval: str,
    direction: str,
    volume_multiplier: float | None,
) -> dict[str, float | str] | None:
    rows = connection.execute(
        """
        SELECT
            bar_intraday.bar_end_ts_utc,
            bar_intraday.close,
            bar_intraday.volume_raw
        FROM instrument
        JOIN bar_intraday
            ON bar_intraday.instrument_id = instrument.instrument_id
        WHERE instrument.market = ?
            AND instrument.symbol = ?
            AND instrument.is_active = TRUE
            AND bar_intraday.interval = ?
            AND bar_intraday.is_closed_bar = TRUE
            AND bar_intraday.close IS NOT NULL
        ORDER BY bar_intraday.bar_start_ts_utc DESC
        LIMIT 31
        """,
        (market, symbol, interval),
    ).fetchall()
    bars = list(reversed(rows))
    if len(bars) < 12:
        return None
    closes = [_optional_float(row["close"]) for row in bars]
    if any(close is None for close in closes):
        return None
    close_values = [float(close) for close in closes if close is not None]
    previous_close = close_values[-2]
    latest_close = close_values[-1]
    previous_ma11 = _mean(close_values[-12:-1])
    latest_ma11 = _mean(close_values[-11:])
    if previous_ma11 is None or latest_ma11 is None:
        return None
    if direction == "up" and not (
        previous_close <= previous_ma11 and latest_close > latest_ma11
    ):
        return None
    if direction == "down" and not (
        previous_close >= previous_ma11 and latest_close < latest_ma11
    ):
        return None
    volume_ratio = 0.0
    if volume_multiplier is not None:
        previous_volumes = [
            float(volume)
            for volume in (_optional_float(row["volume_raw"]) for row in bars[-21:-1])
            if volume is not None
        ]
        latest_volume = _optional_float(bars[-1]["volume_raw"])
        previous_volume_average = _mean(previous_volumes)
        if (
            latest_volume is None
            or previous_volume_average is None
            or previous_volume_average <= 0
        ):
            return None
        volume_ratio = latest_volume / previous_volume_average
        if volume_ratio < volume_multiplier:
            return None
    return {
        "close": latest_close,
        "ma11": latest_ma11,
        "volume_ratio": volume_ratio,
        "bar_key": _format_row_temporal_key(bars[-1]["bar_end_ts_utc"]),
    }


def _latest_daily_ma11_cross(
    connection: sqlite3.Connection,
    *,
    market: str,
    symbol: str,
    direction: str,
) -> dict[str, float | str] | None:
    rows = connection.execute(
        """
        SELECT bar_daily.trade_date, bar_daily.close
        FROM instrument
        JOIN bar_daily
            ON bar_daily.instrument_id = instrument.instrument_id
        WHERE instrument.market = ?
            AND instrument.symbol = ?
            AND instrument.is_active = TRUE
            AND bar_daily.close IS NOT NULL
        ORDER BY bar_daily.trade_date DESC
        LIMIT 12
        """,
        (market, symbol),
    ).fetchall()
    bars = list(reversed(rows))
    if len(bars) < 12:
        return None
    closes = [_optional_float(row["close"]) for row in bars]
    if any(close is None for close in closes):
        return None
    close_values = [float(close) for close in closes if close is not None]
    previous_close = close_values[-2]
    latest_close = close_values[-1]
    previous_ma11 = _mean(close_values[-12:-1])
    latest_ma11 = _mean(close_values[-11:])
    if previous_ma11 is None or latest_ma11 is None:
        return None
    if direction == "up" and not (
        previous_close <= previous_ma11 and latest_close > latest_ma11
    ):
        return None
    return {
        "close": latest_close,
        "ma11": latest_ma11,
        "volume_ratio": 0.0,
        "bar_key": _format_row_temporal_key(bars[-1]["trade_date"]),
    }


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


def _insert_mobile_alert_event(
    connection: sqlite3.Connection,
    *,
    rule_id: int,
    now_utc: str,
    observed_value: float,
    message: str,
    dedupe_key: str | None,
    alert_metadata: dict[str, object],
) -> int | None:
    if dedupe_key is not None:
        existing = connection.execute(
            """
            SELECT mobile_alert_event_id
            FROM mobile_alert_event
            WHERE dedupe_key = ?
            LIMIT 1
            """,
            (dedupe_key,),
        ).fetchone()
        if existing is not None:
            return None
    inserted_row = connection.execute(
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
        VALUES (?, ?, ?, ?, ?, ?, ?)
        RETURNING mobile_alert_event_id
        """,
        (
            rule_id,
            now_utc,
            observed_value,
            message,
            dedupe_key,
            _serialize_alert_metadata(connection, alert_metadata),
            "pending",
        ),
    ).fetchone()
    if inserted_row is None:
        raise RuntimeError("mobile alert event insert did not return a row")
    return int(inserted_row["mobile_alert_event_id"])


def _serialize_alert_metadata(
    connection: sqlite3.Connection,
    metadata: dict[str, object],
) -> object:
    if getattr(connection, "backend", "sqlite") == "postgres":
        try:
            from psycopg.types.json import Jsonb
        except ImportError:
            return json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
        return Jsonb(metadata)
    return json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))


def _mobile_push_message_for_event(
    row: sqlite3.Row,
    *,
    event_id: int,
    title_suffix: str,
    body: str,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "mobile_alert_event_id": event_id,
        "market": str(row["market"]),
        "symbol": str(row["symbol"]),
        "condition_type": str(row["condition_type"]),
        "url": (
            "/instrument.html?"
            f"market={row['market']}&symbol={row['symbol']}"
        ),
    }
    if metadata:
        period = metadata.get("period")
        bar_time = metadata.get("bar_time")
        if isinstance(period, str) and period:
            data["period"] = period
        if isinstance(bar_time, str) and bar_time:
            data["bar_time"] = bar_time
    return {
        "mobile_alert_event_id": event_id,
        "mobile_alert_rule_id": int(row["mobile_alert_rule_id"]),
        "push_device_id": int(row["push_device_id"]),
        "push_token": str(row["push_token"]),
        "getui_cid": (
            str(row["getui_cid"]) if row["getui_cid"] is not None else None
        ),
        "title": f"{row['symbol']} {title_suffix}",
        "body": body,
        "sound": "default",
        "channelId": "market-alerts",
        "data": data,
    }


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


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _format_row_temporal_key(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _ensure_mobile_alert_event_dedupe_column(connection: sqlite3.Connection) -> None:
    if getattr(connection, "backend", "sqlite") == "postgres":
        column = connection.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'mobile_alert_event'
                AND column_name = 'dedupe_key'
            LIMIT 1
            """
        ).fetchone()
        if column is None:
            connection.execute("ALTER TABLE mobile_alert_event ADD COLUMN dedupe_key TEXT")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_mobile_alert_event_rule_dedupe
                ON mobile_alert_event (mobile_alert_rule_id, dedupe_key)
                WHERE dedupe_key IS NOT NULL
                """
            )
        metadata_column = connection.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'mobile_alert_event'
                AND column_name = 'alert_metadata'
            LIMIT 1
            """
        ).fetchone()
        if metadata_column is None:
            connection.execute(
                "ALTER TABLE mobile_alert_event ADD COLUMN alert_metadata JSONB NOT NULL DEFAULT '{}'::jsonb"
            )
        return
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(mobile_alert_event)").fetchall()
    }
    if "dedupe_key" not in columns:
        connection.execute("ALTER TABLE mobile_alert_event ADD COLUMN dedupe_key TEXT")
    if "alert_metadata" not in columns:
        connection.execute(
            "ALTER TABLE mobile_alert_event ADD COLUMN alert_metadata TEXT NOT NULL DEFAULT '{}'"
        )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mobile_alert_event_rule_dedupe
        ON mobile_alert_event (mobile_alert_rule_id, dedupe_key)
        WHERE dedupe_key IS NOT NULL
        """
    )
