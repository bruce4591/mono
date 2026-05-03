from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from market.models import Instrument, IntradayBar, MarketSnapshot
from market.repositories import InstrumentRepository, IntradayBarRepository

BINANCE_FUTURES_API_BASE = "https://fapi.binance.com"
FUTURES_TRADEFI_WATCHLIST = (
    "COINUSDT",
    "MSTRUSDT",
)
_ACTIVE_USDT_PERPETUAL_CONTRACT_TYPES = {"PERPETUAL", "TRADIFI_PERPETUAL"}

FuturesRangeKlineFetcher = Callable[[str, str, int, int, int], list[list[object]]]


@dataclass(frozen=True)
class BinanceFuturesSyncResult:
    symbol: str
    interval: str
    bars: int
    latest_close: float | None


def fetch_binance_futures_24hr_tickers(
    *,
    base_url: str = BINANCE_FUTURES_API_BASE,
    timeout: float = 15.0,
) -> list[dict[str, object]]:
    request = Request(
        f"{base_url}/fapi/v1/ticker/24hr",
        headers={"User-Agent": "market-mvp/0.1"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("unexpected Binance futures 24hr ticker response")
    return [item for item in payload if isinstance(item, dict)]


def fetch_binance_futures_exchange_info(
    *,
    base_url: str = BINANCE_FUTURES_API_BASE,
    timeout: float = 15.0,
) -> dict[str, dict[str, object]]:
    request = Request(
        f"{base_url}/fapi/v1/exchangeInfo",
        headers={"User-Agent": "market-mvp/0.1"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("unexpected Binance futures exchangeInfo response")
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        return {}
    return {
        str(item["symbol"]).upper(): item
        for item in symbols
        if isinstance(item, dict) and item.get("symbol") is not None
    }


def fetch_binance_futures_klines_range(
    symbol: str,
    interval: str,
    start_time_ms: int,
    end_time_ms: int,
    limit: int,
    *,
    base_url: str = BINANCE_FUTURES_API_BASE,
    timeout: float = 15.0,
) -> list[list[object]]:
    query = urlencode(
        {
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
            "limit": limit,
        }
    )
    request = Request(
        f"{base_url}/fapi/v1/klines?{query}",
        headers={"User-Agent": "market-mvp/0.1"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("unexpected Binance futures kline response")
    return payload


def binance_futures_symbol_to_instrument(symbol_info: dict[str, object]) -> Instrument:
    symbol = str(symbol_info.get("symbol", "")).upper()
    quote_asset = str(symbol_info.get("quoteAsset") or "USDT").upper()
    return Instrument(
        market="CRYPTO_FUTURES",
        symbol=symbol,
        display_name=symbol,
        exchange="BINANCE",
        instrument_type="crypto_futures",
        quote_currency=quote_asset,
        timezone="UTC",
        extra_meta={
            "contract_type": symbol_info.get("contractType"),
            "margin_asset": symbol_info.get("marginAsset"),
            "underlying_type": symbol_info.get("underlyingType"),
            "underlying_sub_type": symbol_info.get("underlyingSubType") or [],
        },
    )


def parse_binance_futures_kline(
    *,
    instrument_id: int,
    interval: str,
    row: list[object],
    timezone_name: str,
    now_ms: int | None = None,
    source: str = "binance_futures",
) -> IntradayBar:
    if len(row) < 8:
        raise ValueError("Binance futures kline row must include at least 8 fields")
    open_time_ms = int(row[0])
    close_time_ms = int(row[6])
    resolved_now_ms = now_ms or int(datetime.now(tz=UTC).timestamp() * 1000)
    timezone = ZoneInfo(timezone_name)
    local_start = datetime.fromtimestamp(open_time_ms / 1000, tz=UTC).astimezone(timezone)
    return IntradayBar(
        instrument_id=instrument_id,
        interval=interval,
        bar_start_ts_utc=_format_utc_ms(open_time_ms),
        bar_end_ts_utc=_format_utc_ms(close_time_ms + 1),
        trade_date_local=local_start.date().isoformat(),
        open=_optional_float(row[1]),
        high=_optional_float(row[2]),
        low=_optional_float(row[3]),
        close=_optional_float(row[4]),
        volume_raw=_optional_float(row[5]),
        turnover_raw=_optional_float(row[7]),
        is_closed_bar=close_time_ms < resolved_now_ms,
        source=source,
    )


def sync_binance_futures_klines_range(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    interval: str,
    start_time_ms: int,
    end_time_ms: int,
    limit: int,
    now_ms: int | None = None,
    source: str = "binance_futures_gap_fill",
    fetcher: FuturesRangeKlineFetcher = fetch_binance_futures_klines_range,
) -> BinanceFuturesSyncResult:
    normalized_symbol = symbol.upper()
    instrument = binance_futures_symbol_to_instrument({"symbol": normalized_symbol})
    instrument_id = InstrumentRepository(connection).upsert(instrument)
    rows = fetcher(normalized_symbol, interval, start_time_ms, end_time_ms, limit)
    resolved_now_ms = now_ms or int(datetime.now(tz=UTC).timestamp() * 1000)

    bars = [
        parse_binance_futures_kline(
            instrument_id=instrument_id,
            interval=interval,
            row=row,
            timezone_name=instrument.timezone,
            now_ms=resolved_now_ms,
            source=source,
        )
        for row in rows
    ]
    repository = IntradayBarRepository(connection)
    for bar in bars:
        repository.upsert(bar)

    latest = bars[-1] if bars else None
    return BinanceFuturesSyncResult(
        symbol=normalized_symbol,
        interval=interval,
        bars=len(bars),
        latest_close=latest.close if latest else None,
    )


def parse_binance_futures_24hr_ticker_snapshot(
    *,
    instrument_id: int,
    instrument: Instrument,
    ticker: dict[str, object],
    snapshot_ts_utc: str,
    trade_date_local: str,
) -> MarketSnapshot:
    return MarketSnapshot(
        instrument_id=instrument_id,
        snapshot_ts_utc=snapshot_ts_utc,
        trade_date_local=trade_date_local,
        last_price=_optional_float(ticker.get("lastPrice")),
        change_pct=_optional_float(ticker.get("priceChangePercent")),
        volume_raw=_optional_float(ticker.get("volume")),
        turnover_raw=_optional_float(ticker.get("quoteVolume")),
        quote_currency=instrument.quote_currency,
        source="binance_futures_24hr",
    )


def select_top_futures_usdt_symbols(
    tickers: list[dict[str, object]],
    exchange_info: dict[str, dict[str, object]],
    *,
    limit: int,
) -> list[str]:
    return _select_ranked_futures_symbols(
        tickers,
        exchange_info,
        limit=limit,
        allowed_symbols=None,
    )


def select_futures_tradefi_symbols(
    tickers: list[dict[str, object]],
    exchange_info: dict[str, dict[str, object]],
    *,
    limit: int,
) -> list[str]:
    metadata_symbols = {
        symbol
        for symbol, info in exchange_info.items()
        if _is_tradefi_symbol(info)
    }
    allowed_symbols = metadata_symbols or set(FUTURES_TRADEFI_WATCHLIST)
    return _select_ranked_futures_symbols(
        tickers,
        exchange_info,
        limit=limit,
        allowed_symbols=allowed_symbols,
    )


def _select_ranked_futures_symbols(
    tickers: list[dict[str, object]],
    exchange_info: dict[str, dict[str, object]],
    *,
    limit: int,
    allowed_symbols: set[str] | None,
) -> list[str]:
    rows: list[tuple[str, float]] = []
    for ticker in tickers:
        symbol = str(ticker.get("symbol", "")).upper()
        info = exchange_info.get(symbol, {})
        if allowed_symbols is not None and symbol not in allowed_symbols:
            continue
        if not _is_active_usdt_perpetual(info):
            continue
        rows.append((symbol, float(ticker.get("quoteVolume") or 0)))
    rows.sort(key=lambda row: (-row[1], row[0]))
    return [symbol for symbol, _quote_volume in rows[:limit]]


def _is_active_usdt_perpetual(symbol_info: dict[str, object]) -> bool:
    return (
        str(symbol_info.get("contractType", "")).upper()
        in _ACTIVE_USDT_PERPETUAL_CONTRACT_TYPES
        and str(symbol_info.get("status", "")).upper() == "TRADING"
        and str(symbol_info.get("quoteAsset", "")).upper() == "USDT"
    )


def _is_tradefi_symbol(symbol_info: dict[str, object]) -> bool:
    subtypes = symbol_info.get("underlyingSubType")
    if not isinstance(subtypes, Iterable) or isinstance(subtypes, (str, bytes)):
        return False
    return any(str(item).lower() in {"tradfi", "stock"} for item in subtypes)


def _optional_float(value) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _format_utc_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
