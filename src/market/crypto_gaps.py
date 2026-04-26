from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from market.aggregators import aggregate_crypto_from_1m
from market.binance import (
    RangeKlineFetcher,
    binance_symbol_to_instrument,
    fetch_binance_klines_range,
    sync_binance_klines_range,
)
from market.collectors.base import RequestRateLimiter
from market.repositories import InstrumentRepository

ONE_MINUTE_MS = 60_000
BINANCE_KLINES_MAX_LIMIT = 1000
BINANCE_GAP_FILL_MIN_REQUEST_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class GapFillResult:
    symbols_checked: int
    gaps_filled: int
    bars_written: int
    aggregate_bars_written: int


def fill_binance_1m_gaps(
    connection: sqlite3.Connection,
    *,
    symbols: list[str],
    start_ts_utc: str,
    end_ts_utc: str,
    now_ms: int | None = None,
    fetcher: RangeKlineFetcher = fetch_binance_klines_range,
    min_request_interval_seconds: float = BINANCE_GAP_FILL_MIN_REQUEST_INTERVAL_SECONDS,
) -> GapFillResult:
    normalized_symbols = [symbol.upper() for symbol in symbols]
    start = _floor_minute(_parse_utc(start_ts_utc))
    end = _floor_minute(_parse_utc(end_ts_utc))
    if end <= start or not normalized_symbols:
        return GapFillResult(
            symbols_checked=len(normalized_symbols),
            gaps_filled=0,
            bars_written=0,
            aggregate_bars_written=0,
        )

    limiter = RequestRateLimiter(min_request_interval_seconds)
    gaps_filled = 0
    bars_written = 0
    for symbol in normalized_symbols:
        instrument_id = InstrumentRepository(connection).upsert(
            binance_symbol_to_instrument(symbol)
        )
        for gap in _missing_ranges(connection, instrument_id, start=start, end=end):
            for chunk in _chunk_gap(gap):
                limiter.wait()
                result = sync_binance_klines_range(
                    connection,
                    symbol=symbol,
                    interval="1m",
                    start_time_ms=_to_ms(chunk.start),
                    end_time_ms=_to_ms(chunk.end) - 1,
                    limit=chunk.minutes,
                    now_ms=now_ms,
                    fetcher=fetcher,
                )
                gaps_filled += chunk.minutes
                bars_written += result.bars

    aggregate_result = aggregate_crypto_from_1m(connection, normalized_symbols)
    return GapFillResult(
        symbols_checked=len(normalized_symbols),
        gaps_filled=gaps_filled,
        bars_written=bars_written,
        aggregate_bars_written=aggregate_result.bars_written,
    )


@dataclass(frozen=True)
class _GapRange:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


def _missing_ranges(
    connection: sqlite3.Connection,
    instrument_id: int,
    *,
    start: datetime,
    end: datetime,
) -> list[_GapRange]:
    existing = {
        _parse_utc(str(row["bar_start_ts_utc"]))
        for row in connection.execute(
            """
            SELECT bar_start_ts_utc
            FROM bar_intraday
            WHERE instrument_id = ?
                AND interval = '1m'
                AND bar_start_ts_utc >= ?
                AND bar_start_ts_utc < ?
            """,
            (instrument_id, _format_utc(start), _format_utc(end)),
        ).fetchall()
    }
    missing = []
    cursor = start
    while cursor < end:
        if cursor not in existing:
            missing.append(cursor)
        cursor += timedelta(minutes=1)
    return _contiguous_ranges(missing)


def _contiguous_ranges(starts: list[datetime]) -> list[_GapRange]:
    if not starts:
        return []
    ranges = []
    range_start = starts[0]
    previous = starts[0]
    for current in starts[1:]:
        if current - previous != timedelta(minutes=1):
            ranges.append(_GapRange(start=range_start, end=previous + timedelta(minutes=1)))
            range_start = current
        previous = current
    ranges.append(_GapRange(start=range_start, end=previous + timedelta(minutes=1)))
    return ranges


def _chunk_gap(gap: _GapRange) -> list[_GapRange]:
    chunks = []
    start = gap.start
    while start < gap.end:
        end = min(start + timedelta(minutes=BINANCE_KLINES_MAX_LIMIT), gap.end)
        chunks.append(_GapRange(start=start, end=end))
        start = end
    return chunks


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _floor_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _format_utc(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)
