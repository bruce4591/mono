from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from market.collectors.base import CollectorResult
from market.models import Instrument, MarketSnapshot
from market.repositories import InstrumentRepository, MarketSnapshotRepository


@dataclass(frozen=True)
class MacroInstrumentConfig:
    market: str
    symbol: str
    display_name: str
    exchange: str
    instrument_type: str
    quote_currency: str
    timezone: str
    sort_order: int
    unit: str | None = None
    metric_label: str | None = None


RATE_INSTRUMENTS = (
    MacroInstrumentConfig("MACRO_RATE", "US2Y", "US Treasury 2Y", "AKSHARE", "yield", "PCT", "UTC", 1, "%", "Yield"),
    MacroInstrumentConfig("MACRO_RATE", "US5Y", "US Treasury 5Y", "AKSHARE", "yield", "PCT", "UTC", 2, "%", "Yield"),
    MacroInstrumentConfig("MACRO_RATE", "US10Y", "US Treasury 10Y", "AKSHARE", "yield", "PCT", "UTC", 3, "%", "Yield"),
    MacroInstrumentConfig("MACRO_RATE", "US30Y", "US Treasury 30Y", "AKSHARE", "yield", "PCT", "UTC", 4, "%", "Yield"),
    MacroInstrumentConfig("MACRO_RATE", "CN10Y", "China Treasury 10Y", "AKSHARE", "yield", "PCT", "Asia/Shanghai", 5, "%", "Yield"),
    MacroInstrumentConfig("MACRO_RATE", "CN30Y", "China Treasury 30Y", "AKSHARE", "yield", "PCT", "Asia/Shanghai", 6, "%", "Yield"),
)

FX_INSTRUMENTS = (
    MacroInstrumentConfig("FX", "USDJPY", "USD/JPY", "SAFE", "fx", "JPY", "Asia/Shanghai", 1, None, "Rate"),
    MacroInstrumentConfig("FX", "USDCNY", "USD/CNY", "SAFE", "fx", "CNY", "Asia/Shanghai", 2, None, "Rate"),
    MacroInstrumentConfig("FX", "EURUSD", "EUR/USD", "SAFE", "fx", "USD", "Asia/Shanghai", 3, None, "Rate"),
    MacroInstrumentConfig("FX", "GBPUSD", "GBP/USD", "SAFE", "fx", "USD", "Asia/Shanghai", 4, None, "Rate"),
    MacroInstrumentConfig("FX", "AUDUSD", "AUD/USD", "SAFE", "fx", "USD", "Asia/Shanghai", 5, None, "Rate"),
    MacroInstrumentConfig("FX", "USDCAD", "USD/CAD", "SAFE", "fx", "CAD", "Asia/Shanghai", 6, None, "Rate"),
)

RATE_COLUMNS = {
    "US2Y": "美国国债收益率2年",
    "US5Y": "美国国债收益率5年",
    "US10Y": "美国国债收益率10年",
    "US30Y": "美国国债收益率30年",
    "CN10Y": "中国国债收益率10年",
    "CN30Y": "中国国债收益率30年",
}


def list_macro_board_payload(
    connection: sqlite3.Connection,
    *,
    key: str,
    title: str,
    market: str,
    instruments: tuple[MacroInstrumentConfig, ...],
) -> dict[str, object]:
    items = []
    data_times: list[str] = []
    for config in instruments:
        row = connection.execute(
            """
            SELECT
                instrument.market,
                instrument.symbol,
                instrument.display_name,
                latest_market_snapshot.last_price,
                latest_market_snapshot.change_pct,
                latest_market_snapshot.volume_raw,
                latest_market_snapshot.turnover_raw,
                latest_market_snapshot.snapshot_ts_utc
            FROM instrument
            JOIN latest_market_snapshot
                ON latest_market_snapshot.instrument_id = instrument.instrument_id
            WHERE instrument.market = ?
                AND instrument.symbol = ?
            """,
            (config.market, config.symbol),
        ).fetchone()
        if row is None:
            continue
        data_time = str(row["snapshot_ts_utc"])
        data_times.append(data_time)
        items.append(
            {
                "market": str(row["market"]),
                "symbol": str(row["symbol"]),
                "name": str(row["display_name"]),
                "last_price": _optional_float(row["last_price"]),
                "change_pct": _optional_float(row["change_pct"]),
                "turnover": _optional_float(row["turnover_raw"]),
                "volume": _optional_float(row["volume_raw"]),
                "rank": config.sort_order,
                "rank_change": None,
                "data_time": data_time,
                "unit": config.unit,
                "metric_label": config.metric_label,
            }
        )
    return {
        "key": key,
        "title": title,
        "market": market,
        "data_time": max(data_times) if data_times else None,
        "items": items,
    }


def sync_macro_boards(
    connection: sqlite3.Connection,
    *,
    bond_rate_frame=None,
    fx_safe_frame=None,
    snapshot_ts_utc: str | None = None,
) -> CollectorResult:
    if bond_rate_frame is None or fx_safe_frame is None:
        akshare = _load_akshare()
        if bond_rate_frame is None:
            bond_rate_frame = akshare.bond_zh_us_rate()
        if fx_safe_frame is None:
            fx_safe_frame = akshare.currency_boc_safe()

    resolved_snapshot = snapshot_ts_utc or datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    items_synced = 0
    instruments = InstrumentRepository(connection)
    snapshots = MarketSnapshotRepository(connection)
    for config, trade_date, value, previous_value in _rate_values(bond_rate_frame):
        instrument_id = _upsert_macro_instrument(instruments, config)
        snapshots.upsert(
            _snapshot(
                instrument_id=instrument_id,
                snapshot_ts_utc=resolved_snapshot,
                trade_date_local=trade_date,
                value=value,
                previous_value=previous_value,
                quote_currency=config.quote_currency,
                source="macro_akshare",
                sort_order=config.sort_order,
            )
        )
        items_synced += 1
    for config, trade_date, value, previous_value in _fx_values(fx_safe_frame):
        instrument_id = _upsert_macro_instrument(instruments, config)
        snapshots.upsert(
            _snapshot(
                instrument_id=instrument_id,
                snapshot_ts_utc=resolved_snapshot,
                trade_date_local=trade_date,
                value=value,
                previous_value=previous_value,
                quote_currency=config.quote_currency,
                source="macro_akshare",
                sort_order=config.sort_order,
            )
        )
        items_synced += 1
    connection.commit()
    return CollectorResult(
        source_name="macro_akshare",
        items_synced=items_synced,
        metadata={
            "rates": len(RATE_INSTRUMENTS),
            "fx": len(FX_INSTRUMENTS),
            "snapshot_ts_utc": resolved_snapshot,
        },
    )


def _rate_values(frame) -> list[tuple[MacroInstrumentConfig, str, float, float | None]]:
    records = _records_from_frame(frame)
    values = []
    for config in RATE_INSTRUMENTS:
        column = RATE_COLUMNS[config.symbol]
        latest = _latest_two_values(records, lambda row, c=column: _optional_float(row.get(c)))
        if latest is None:
            continue
        values.append((config, latest[0], latest[1], latest[2]))
    return values


def _fx_values(frame) -> list[tuple[MacroInstrumentConfig, str, float, float | None]]:
    records = _records_from_frame(frame)
    fx_formulas: dict[str, Callable[[dict[str, object]], float | None]] = {
        "USDJPY": lambda row: _ratio(row, "美元", "日元"),
        "USDCNY": lambda row: _divide(_optional_float(row.get("美元")), 100.0),
        "EURUSD": lambda row: _ratio(row, "欧元", "美元"),
        "GBPUSD": lambda row: _ratio(row, "英镑", "美元"),
        "AUDUSD": lambda row: _ratio(row, "澳元", "美元"),
        "USDCAD": lambda row: _ratio(row, "美元", "加元"),
    }
    values = []
    for config in FX_INSTRUMENTS:
        latest = _latest_two_values(records, fx_formulas[config.symbol])
        if latest is None:
            continue
        values.append((config, latest[0], latest[1], latest[2]))
    return values


def _latest_two_values(
    records: list[dict[str, object]],
    value_fn: Callable[[dict[str, object]], float | None],
) -> tuple[str, float, float | None] | None:
    valid = [
        (str(row.get("日期") or row.get("date"))[:10], value)
        for row in records
        for value in [value_fn(row)]
        if row.get("日期") or row.get("date")
        if value is not None
    ]
    if not valid:
        return None
    valid.sort(key=lambda item: item[0])
    trade_date, value = valid[-1]
    previous_value = valid[-2][1] if len(valid) >= 2 else None
    return trade_date, value, previous_value


def _snapshot(
    *,
    instrument_id: int,
    snapshot_ts_utc: str,
    trade_date_local: str,
    value: float,
    previous_value: float | None,
    quote_currency: str,
    source: str,
    sort_order: int,
) -> MarketSnapshot:
    return MarketSnapshot(
        instrument_id=instrument_id,
        snapshot_ts_utc=snapshot_ts_utc,
        trade_date_local=trade_date_local,
        last_price=value,
        change_pct=_percent_change(previous_value, value),
        volume_raw=None,
        turnover_raw=float(sort_order),
        quote_currency=quote_currency,
        source=source,
    )


def _upsert_macro_instrument(repository: InstrumentRepository, config: MacroInstrumentConfig) -> int:
    return repository.upsert(
        Instrument(
            market=config.market,
            symbol=config.symbol,
            display_name=config.display_name,
            exchange=config.exchange,
            instrument_type=config.instrument_type,
            quote_currency=config.quote_currency,
            timezone=config.timezone,
            extra_meta={"sort_order": config.sort_order, "unit": config.unit},
        )
    )


def _records_from_frame(frame) -> list[dict[str, object]]:
    if hasattr(frame, "to_dict"):
        return list(frame.to_dict("records"))
    return list(frame)


def _ratio(row: dict[str, object], numerator: str, denominator: str) -> float | None:
    return _divide(_optional_float(row.get(numerator)), _optional_float(row.get(denominator)))


def _divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _optional_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(resolved):
        return None
    return resolved


def _percent_change(previous: float | None, current: float | None) -> float | None:
    if previous in (None, 0) or current is None:
        return None
    return ((current - previous) / previous) * 100


def _load_akshare():
    try:
        import akshare
    except ImportError as error:
        raise RuntimeError("akshare dependency is required for sync-macro-boards") from error
    return akshare
