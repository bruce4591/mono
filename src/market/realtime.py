from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from market.aggregators import aggregate_crypto_from_1m, aggregate_market_from_1m
from market.binance import binance_symbol_to_instrument
from market.binance_futures import binance_futures_symbol_to_instrument
from market.models import IntradayBar, MarketSnapshot
from market.repositories import (
    InstrumentRepository,
    IntradayBarRepository,
    MarketSnapshotRepository,
)


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


@dataclass(frozen=True)
class BinanceFuturesTradeEvent:
    symbol: str
    timestamp: float
    price: float
    size: float
    side: str


@dataclass(frozen=True)
class BinanceFuturesDepthEvent:
    symbol: str
    timestamp: float
    bids: list[tuple[str, str]]
    asks: list[tuple[str, str]]


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


def parse_binance_futures_trade_event(payload: dict[str, Any]) -> BinanceFuturesTradeEvent:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("Binance futures trade payload must be an object")
    if str(data.get("e")) != "aggTrade":
        raise ValueError("Binance futures trade payload must be aggTrade")
    trade_time_ms = int(data.get("T") or data["E"])
    maker_is_buyer = bool(data.get("m"))
    return BinanceFuturesTradeEvent(
        symbol=str(data["s"]).upper(),
        timestamp=trade_time_ms / 1000.0,
        price=float(data["p"]),
        size=float(data["q"]),
        side="Sell" if maker_is_buyer else "Buy",
    )


def parse_binance_futures_depth_event(payload: dict[str, Any]) -> BinanceFuturesDepthEvent:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("Binance futures depth payload must be an object")
    event_time_ms = int(data.get("T") or data["E"])
    bids = data.get("b")
    asks = data.get("a")
    if not isinstance(bids, list) or not isinstance(asks, list):
        raise ValueError("Binance futures depth payload must include bids and asks")
    return BinanceFuturesDepthEvent(
        symbol=str(data["s"]).upper(),
        timestamp=event_time_ms / 1000.0,
        bids=[(str(price), str(quantity)) for price, quantity in bids],
        asks=[(str(price), str(quantity)) for price, quantity in asks],
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


def parse_binance_kline_event(
    payload: dict[str, Any],
    *,
    instrument_id: int,
    timezone_name: str,
) -> IntradayBar:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("Binance kline payload must be an object")
    kline = data.get("k")
    if not isinstance(kline, dict):
        raise ValueError("Binance kline payload must include kline object")

    open_time_ms = int(kline["t"])
    close_time_ms = int(kline["T"])
    timezone = ZoneInfo(timezone_name)
    local_start = datetime.fromtimestamp(open_time_ms / 1000, tz=UTC).astimezone(timezone)
    return IntradayBar(
        instrument_id=instrument_id,
        interval=str(kline["i"]),
        bar_start_ts_utc=_format_utc_ms(open_time_ms),
        bar_end_ts_utc=_format_utc_ms(close_time_ms + 1),
        trade_date_local=local_start.date().isoformat(),
        open=float(kline["o"]),
        high=float(kline["h"]),
        low=float(kline["l"]),
        close=float(kline["c"]),
        volume_raw=_optional_float(kline.get("v")),
        turnover_raw=_optional_float(kline.get("q")),
        is_closed_bar=bool(kline["x"]),
        source="binance_ws_kline",
    )


def apply_binance_kline_event(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    aggregate: bool = True,
) -> IntradayBar:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("Binance kline payload must be an object")
    kline = data.get("k")
    if not isinstance(kline, dict):
        raise ValueError("Binance kline payload must include kline object")

    symbol = str(kline.get("s") or data["s"]).upper()
    event_time_ms = int(data["E"])
    instrument = binance_symbol_to_instrument(symbol)
    instrument_id = InstrumentRepository(connection).upsert(instrument)
    bar = parse_binance_kline_event(
        payload,
        instrument_id=instrument_id,
        timezone_name=instrument.timezone,
    )
    IntradayBarRepository(connection).upsert(bar)
    if aggregate and bar.interval == "1m":
        aggregate_crypto_from_1m(connection, [symbol])
    _upsert_kline_price_snapshot(
        connection,
        instrument_id=instrument_id,
        snapshot_ts_utc=_format_utc_ms(event_time_ms),
        trade_date_local=bar.trade_date_local,
        last_price=bar.close,
        fallback_volume_raw=bar.volume_raw,
        fallback_turnover_raw=bar.turnover_raw,
        quote_currency=instrument.quote_currency,
    )
    return bar


def apply_binance_futures_kline_event(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    aggregate: bool = True,
) -> IntradayBar:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("Binance kline payload must be an object")
    kline = data.get("k")
    if not isinstance(kline, dict):
        raise ValueError("Binance kline payload must include kline object")

    symbol = str(kline.get("s") or data["s"]).upper()
    event_time_ms = int(data["E"])
    instrument = binance_futures_symbol_to_instrument({"symbol": symbol})
    instrument_id = InstrumentRepository(connection).upsert(instrument)
    bar = replace(
        parse_binance_kline_event(
            payload,
            instrument_id=instrument_id,
            timezone_name=instrument.timezone,
        ),
        source="binance_futures_ws_kline",
    )
    IntradayBarRepository(connection).upsert(bar)
    if aggregate and bar.interval == "1m":
        aggregate_market_from_1m(connection, market="CRYPTO_FUTURES", symbols=[symbol])
    _upsert_kline_price_snapshot(
        connection,
        instrument_id=instrument_id,
        snapshot_ts_utc=_format_utc_ms(event_time_ms),
        trade_date_local=bar.trade_date_local,
        last_price=bar.close,
        fallback_volume_raw=bar.volume_raw,
        fallback_turnover_raw=bar.turnover_raw,
        quote_currency=instrument.quote_currency,
        source="binance_futures_ws_kline_price",
    )
    return bar


def _upsert_kline_price_snapshot(
    connection: sqlite3.Connection,
    *,
    instrument_id: int,
    snapshot_ts_utc: str,
    trade_date_local: str,
    last_price: float | None,
    fallback_volume_raw: float | None,
    fallback_turnover_raw: float | None,
    quote_currency: str,
    source: str = "binance_ws_kline_price",
) -> None:
    existing = connection.execute(
        """
        SELECT change_pct, volume_raw, turnover_raw, quote_currency
        FROM market_snapshot
        WHERE instrument_id = ?
            AND trade_date_local = ?
        """,
        (instrument_id, trade_date_local),
    ).fetchone()
    MarketSnapshotRepository(connection).upsert(
        MarketSnapshot(
            instrument_id=instrument_id,
            snapshot_ts_utc=snapshot_ts_utc,
            trade_date_local=trade_date_local,
            last_price=last_price,
            change_pct=_optional_float(existing["change_pct"]) if existing else None,
            volume_raw=(
                _optional_float(existing["volume_raw"])
                if existing
                else fallback_volume_raw
            ),
            turnover_raw=(
                _optional_float(existing["turnover_raw"])
                if existing
                else fallback_turnover_raw
            ),
            quote_currency=str(existing["quote_currency"]) if existing else quote_currency,
            source=source,
        )
    )


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
