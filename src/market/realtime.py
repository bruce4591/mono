from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from market.binance import binance_symbol_to_instrument
from market.models import MarketSnapshot
from market.repositories import InstrumentRepository, MarketSnapshotRepository


@dataclass(frozen=True)
class BinanceTickerEvent:
    symbol: str
    snapshot_ts_utc: str
    trade_date_local: str
    last_price: float
    change_pct: float | None
    volume_raw: float | None
    turnover_raw: float | None
    quote_currency: str


def parse_binance_ticker_event(payload: dict[str, Any]) -> BinanceTickerEvent:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("Binance ticker payload must be an object")

    symbol = str(data["s"]).upper()
    instrument = binance_symbol_to_instrument(symbol)
    event_time_ms = int(data["E"])
    last_price = float(data["c"])
    change_pct = _change_pct(data, last_price)

    return BinanceTickerEvent(
        symbol=symbol,
        snapshot_ts_utc=_format_utc_ms(event_time_ms),
        trade_date_local=_trade_date_utc_ms(event_time_ms),
        last_price=last_price,
        change_pct=change_pct,
        volume_raw=_optional_float(data.get("v")),
        turnover_raw=_optional_float(data.get("q")),
        quote_currency=instrument.quote_currency,
    )


def apply_binance_ticker_event(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
) -> BinanceTickerEvent:
    event = parse_binance_ticker_event(payload)
    instrument = binance_symbol_to_instrument(event.symbol)
    instrument_id = InstrumentRepository(connection).upsert(instrument)
    MarketSnapshotRepository(connection).upsert(
        MarketSnapshot(
            instrument_id=instrument_id,
            snapshot_ts_utc=event.snapshot_ts_utc,
            trade_date_local=event.trade_date_local,
            last_price=event.last_price,
            change_pct=event.change_pct,
            volume_raw=event.volume_raw,
            turnover_raw=event.turnover_raw,
            quote_currency=event.quote_currency,
            source="binance_ws",
        )
    )
    return event


def _change_pct(data: dict[str, Any], last_price: float) -> float | None:
    if data.get("P") is not None:
        return float(data["P"])
    open_price = _optional_float(data.get("o"))
    if open_price in (None, 0):
        return None
    return ((last_price - open_price) / open_price) * 100


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _format_utc_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trade_date_utc_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).date().isoformat()
