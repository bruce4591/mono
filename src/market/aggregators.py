from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from market.models import DailyBar, IntradayBar
from market.repositories import DailyBarRepository, IntradayBarRepository


@dataclass(frozen=True)
class AggregationResult:
    bars_written: int


def aggregate_intraday_from_1m(
    connection: sqlite3.Connection,
    *,
    instrument_ids: list[int],
    target_intervals: list[str],
) -> AggregationResult:
    bars_written = 0
    repository = IntradayBarRepository(connection)
    for instrument_id in instrument_ids:
        source_bars = repository.list_for_instrument(instrument_id, "1m")
        for target_interval in target_intervals:
            for bar in _aggregate_intraday_bars(source_bars, target_interval):
                repository.upsert(bar)
                bars_written += 1
    return AggregationResult(bars_written=bars_written)


def aggregate_daily_from_intraday(
    connection: sqlite3.Connection,
    *,
    instrument_ids: list[int],
    source_interval: str = "1m",
) -> AggregationResult:
    bars_written = 0
    intraday_repository = IntradayBarRepository(connection)
    daily_repository = DailyBarRepository(connection)
    for instrument_id in instrument_ids:
        source_bars = intraday_repository.list_for_instrument(
            instrument_id,
            source_interval,
        )
        for bar in _aggregate_daily_bars(source_bars):
            daily_repository.upsert(bar)
            bars_written += 1
    return AggregationResult(bars_written=bars_written)


def aggregate_crypto_from_1m(
    connection: sqlite3.Connection,
    symbols: list[str],
) -> AggregationResult:
    if not symbols:
        return AggregationResult(bars_written=0)
    placeholders = ",".join("?" for _symbol in symbols)
    rows = connection.execute(
        f"""
        SELECT instrument_id
        FROM instrument
        WHERE market = 'CRYPTO'
            AND symbol IN ({placeholders})
        ORDER BY symbol
        """,
        [symbol.upper() for symbol in symbols],
    ).fetchall()
    instrument_ids = [int(row["instrument_id"]) for row in rows]
    intraday = aggregate_intraday_from_1m(
        connection,
        instrument_ids=instrument_ids,
        target_intervals=["5m", "15m", "8h"],
    )
    daily = aggregate_daily_from_intraday(
        connection,
        instrument_ids=instrument_ids,
        source_interval="1m",
    )
    return AggregationResult(bars_written=intraday.bars_written + daily.bars_written)


def _aggregate_intraday_bars(
    bars: list[IntradayBar],
    target_interval: str,
) -> list[IntradayBar]:
    window_seconds = _interval_seconds(target_interval)
    grouped: dict[int, list[IntradayBar]] = {}
    for bar in bars:
        start = _parse_utc(bar.bar_start_ts_utc)
        window_start_epoch = int(start.timestamp()) // window_seconds * window_seconds
        grouped.setdefault(window_start_epoch, []).append(bar)

    results = []
    for window_start_epoch in sorted(grouped):
        group = sorted(grouped[window_start_epoch], key=lambda item: item.bar_start_ts_utc)
        window_start = datetime.fromtimestamp(window_start_epoch, tz=UTC)
        window_end = window_start + timedelta(seconds=window_seconds)
        results.append(
            IntradayBar(
                instrument_id=group[0].instrument_id,
                interval=target_interval,
                bar_start_ts_utc=_format_utc(window_start),
                bar_end_ts_utc=_format_utc(window_end),
                trade_date_local=group[0].trade_date_local,
                open=group[0].open,
                high=_max_optional(bar.high for bar in group),
                low=_min_optional(bar.low for bar in group),
                close=group[-1].close,
                volume_raw=_sum_optional(bar.volume_raw for bar in group),
                turnover_raw=_sum_optional(bar.turnover_raw for bar in group),
                is_closed_bar=all(bar.is_closed_bar for bar in group)
                and _parse_utc(group[-1].bar_end_ts_utc) >= window_end,
                source="aggregate_1m",
            )
        )
    return results


def _aggregate_daily_bars(bars: list[IntradayBar]) -> list[DailyBar]:
    grouped: dict[str, list[IntradayBar]] = {}
    for bar in bars:
        grouped.setdefault(bar.trade_date_local, []).append(bar)

    results = []
    for trade_date in sorted(grouped):
        group = sorted(grouped[trade_date], key=lambda item: item.bar_start_ts_utc)
        results.append(
            DailyBar(
                instrument_id=group[0].instrument_id,
                trade_date=trade_date,
                open=group[0].open,
                high=_max_optional(bar.high for bar in group),
                low=_min_optional(bar.low for bar in group),
                close=group[-1].close,
                volume_raw=_sum_optional(bar.volume_raw for bar in group),
                turnover_raw=_sum_optional(bar.turnover_raw for bar in group),
                quote_currency="USDT",
                source="aggregate_1m",
            )
        )
    return results


def _interval_seconds(interval: str) -> int:
    if interval.endswith("m"):
        return int(interval[:-1]) * 60
    if interval.endswith("h"):
        return int(interval[:-1]) * 60 * 60
    raise ValueError(f"unsupported aggregate interval: {interval}")


def _parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _format_utc(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sum_optional(values) -> float | None:
    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    return float(sum(numbers))


def _max_optional(values) -> float | None:
    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    return float(max(numbers))


def _min_optional(values) -> float | None:
    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    return float(min(numbers))
