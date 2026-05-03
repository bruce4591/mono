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
    for instrument_id in instrument_ids:
        for target_interval in target_intervals:
            source_bars = _list_intraday_bars(
                connection,
                instrument_id=instrument_id,
                interval="1m",
                since_ts_utc=_intraday_checkpoint(
                    connection,
                    instrument_id=instrument_id,
                    interval=target_interval,
                ),
            )
            repository = IntradayBarRepository(connection)
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
    daily_repository = DailyBarRepository(connection)
    for instrument_id in instrument_ids:
        source_bars = _list_intraday_bars(
            connection,
            instrument_id,
            source_interval,
            since_trade_date=_daily_checkpoint(
                connection,
                instrument_id=instrument_id,
            ),
        )
        for bar in _aggregate_daily_bars(source_bars):
            daily_repository.upsert(bar)
            bars_written += 1
    return AggregationResult(bars_written=bars_written)


def aggregate_crypto_from_1m(
    connection: sqlite3.Connection,
    symbols: list[str],
) -> AggregationResult:
    return aggregate_market_from_1m(connection, market="CRYPTO", symbols=symbols)


def aggregate_market_from_1m(
    connection: sqlite3.Connection,
    *,
    market: str,
    symbols: list[str],
) -> AggregationResult:
    if not symbols:
        return AggregationResult(bars_written=0)
    placeholders = ",".join("?" for _symbol in symbols)
    rows = connection.execute(
        f"""
        SELECT instrument_id
        FROM instrument
        WHERE market = ?
            AND symbol IN ({placeholders})
        ORDER BY symbol
        """,
        [market, *[symbol.upper() for symbol in symbols]],
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


def _intraday_checkpoint(
    connection: sqlite3.Connection,
    *,
    instrument_id: int,
    interval: str,
) -> str | None:
    row = connection.execute(
        """
        SELECT bar_start_ts_utc, bar_end_ts_utc, is_closed_bar
        FROM bar_intraday
        WHERE instrument_id = ? AND interval = ?
        ORDER BY bar_start_ts_utc DESC
        LIMIT 1
        """,
        (instrument_id, interval),
    ).fetchone()
    if row is None:
        return None
    if bool(row["is_closed_bar"]):
        return str(row["bar_end_ts_utc"])
    return str(row["bar_start_ts_utc"])


def _daily_checkpoint(
    connection: sqlite3.Connection,
    *,
    instrument_id: int,
) -> str | None:
    row = connection.execute(
        """
        SELECT trade_date
        FROM bar_daily
        WHERE instrument_id = ?
        ORDER BY trade_date DESC
        LIMIT 1
        """,
        (instrument_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row["trade_date"])


def _list_intraday_bars(
    connection: sqlite3.Connection,
    instrument_id: int,
    interval: str,
    *,
    since_ts_utc: str | None = None,
    since_trade_date: str | None = None,
) -> list[IntradayBar]:
    filters = ["instrument_id = ?", "interval = ?"]
    params: list[object] = [instrument_id, interval]
    if since_ts_utc is not None:
        filters.append("bar_start_ts_utc >= ?")
        params.append(since_ts_utc)
    if since_trade_date is not None:
        filters.append("trade_date_local >= ?")
        params.append(since_trade_date)
    rows = connection.execute(
        f"""
        SELECT
            instrument_id,
            interval,
            bar_start_ts_utc,
            bar_end_ts_utc,
            trade_date_local,
            open,
            high,
            low,
            close,
            volume_raw,
            turnover_raw,
            is_closed_bar,
            source
        FROM bar_intraday
        WHERE {" AND ".join(filters)}
        ORDER BY bar_start_ts_utc
        """,
        params,
    ).fetchall()
    return [
        IntradayBar(
            instrument_id=int(row["instrument_id"]),
            interval=str(row["interval"]),
            bar_start_ts_utc=str(row["bar_start_ts_utc"]),
            bar_end_ts_utc=str(row["bar_end_ts_utc"]),
            trade_date_local=str(row["trade_date_local"]),
            open=_optional_float(row["open"]),
            high=_optional_float(row["high"]),
            low=_optional_float(row["low"]),
            close=_optional_float(row["close"]),
            volume_raw=_optional_float(row["volume_raw"]),
            turnover_raw=_optional_float(row["turnover_raw"]),
            is_closed_bar=bool(row["is_closed_bar"]),
            source=str(row["source"]),
        )
        for row in rows
    ]


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


def _optional_float(value) -> float | None:
    if value is None:
        return None
    return float(value)
