from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Instrument:
    market: str
    symbol: str
    display_name: str
    exchange: str
    instrument_type: str
    quote_currency: str
    timezone: str
    is_active: bool = True
    extra_meta: dict[str, Any] = field(default_factory=dict)
    instrument_id: int | None = None


@dataclass(frozen=True)
class DailyBar:
    instrument_id: int
    trade_date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume_raw: float | None
    turnover_raw: float | None
    quote_currency: str
    source: str


@dataclass(frozen=True)
class IntradayBar:
    instrument_id: int
    interval: str
    bar_start_ts_utc: str
    bar_end_ts_utc: str
    trade_date_local: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume_raw: float | None
    turnover_raw: float | None
    is_closed_bar: bool
    source: str


@dataclass(frozen=True)
class WatchlistEntry:
    instrument_id: int
    sort_order: int
    is_active: bool = True


@dataclass(frozen=True)
class MarketSnapshot:
    instrument_id: int
    snapshot_ts_utc: str
    trade_date_local: str
    last_price: float | None
    change_pct: float | None
    volume_raw: float | None
    turnover_raw: float | None
    quote_currency: str
    source: str


@dataclass(frozen=True)
class RankingEntry:
    board_name: str
    snapshot_ts_utc: str
    rank: int
    instrument_id: int
    turnover_raw: float
    quote_currency: str
    change_pct: float | None
    source: str
