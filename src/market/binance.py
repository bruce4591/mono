from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from market.models import DailyBar, Instrument, IntradayBar, MarketSnapshot
from market.repositories import (
    DailyBarRepository,
    InstrumentRepository,
    IntradayBarRepository,
    MarketSnapshotRepository,
)

BINANCE_SPOT_API_BASE = "https://api.binance.com"
KNOWN_QUOTE_ASSETS = ("USDT", "USDC", "FDUSD", "TUSD", "BUSD", "BTC", "ETH", "BNB", "USD")

KlineFetcher = Callable[[str, str, int], list[list[object]]]


@dataclass(frozen=True)
class BinanceSyncResult:
    symbol: str
    interval: str
    bars: int
    latest_close: float | None


def fetch_binance_klines(
    symbol: str,
    interval: str,
    limit: int,
    *,
    base_url: str = BINANCE_SPOT_API_BASE,
    timeout: float = 15.0,
) -> list[list[object]]:
    query = urlencode(
        {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit,
        }
    )
    request = Request(
        f"{base_url}/api/v3/klines?{query}",
        headers={"User-Agent": "market-mvp/0.1"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("unexpected Binance kline response")
    return payload


def fetch_binance_24hr_tickers(
    *,
    base_url: str = BINANCE_SPOT_API_BASE,
    timeout: float = 15.0,
) -> list[dict[str, object]]:
    request = Request(
        f"{base_url}/api/v3/ticker/24hr",
        headers={"User-Agent": "market-mvp/0.1"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("unexpected Binance 24hr ticker response")
    return [item for item in payload if isinstance(item, dict)]


def fetch_top_binance_usdt_symbols(limit: int = 50) -> list[str]:
    return select_top_quote_volume_symbols(
        fetch_binance_24hr_tickers(),
        quote_asset="USDT",
        limit=limit,
    )


def select_top_quote_volume_symbols(
    tickers: list[dict[str, object]],
    *,
    quote_asset: str,
    limit: int,
) -> list[str]:
    suffix = quote_asset.upper()
    rows = [
        (str(ticker["symbol"]).upper(), float(ticker.get("quoteVolume") or 0))
        for ticker in tickers
        if str(ticker.get("symbol", "")).upper().endswith(suffix)
    ]
    rows.sort(key=lambda row: (-row[1], row[0]))
    return [symbol for symbol, _quote_volume in rows[:limit]]


def sync_binance_klines(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    interval: str = "15m",
    limit: int = 96,
    now_ms: int | None = None,
    fetcher: KlineFetcher = fetch_binance_klines,
) -> BinanceSyncResult:
    normalized_symbol = symbol.upper()
    instrument = binance_symbol_to_instrument(normalized_symbol)
    instruments = InstrumentRepository(connection)
    instrument_id = instruments.upsert(instrument)
    rows = fetcher(normalized_symbol, interval, limit)
    resolved_now_ms = now_ms or int(datetime.now(tz=UTC).timestamp() * 1000)

    connection.execute(
        """
        DELETE FROM bar_intraday
        WHERE instrument_id = ?
            AND interval = ?
            AND source = 'sample'
        """,
        (instrument_id, interval),
    )
    bars = [
        parse_binance_kline(
            instrument_id=instrument_id,
            interval=interval,
            row=row,
            now_ms=resolved_now_ms,
            timezone_name=instrument.timezone,
        )
        for row in rows
    ]
    bar_repository = IntradayBarRepository(connection)
    for bar in bars:
        bar_repository.upsert(bar)

    latest = bars[-1] if bars else None
    if latest is not None:
        change_pct = _change_pct(bars[-2].close if len(bars) >= 2 else None, latest.close)
        MarketSnapshotRepository(connection).upsert(
            MarketSnapshot(
                instrument_id=instrument_id,
                snapshot_ts_utc=_format_utc_ms(resolved_now_ms),
                trade_date_local=latest.trade_date_local,
                last_price=latest.close,
                change_pct=change_pct,
                volume_raw=latest.volume_raw,
                turnover_raw=latest.turnover_raw,
                quote_currency=instrument.quote_currency,
                source="binance",
            )
        )

    return BinanceSyncResult(
        symbol=normalized_symbol,
        interval=interval,
        bars=len(bars),
        latest_close=latest.close if latest else None,
    )


def sync_binance_daily_bars(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    days: int = 365,
    fetcher: KlineFetcher = fetch_binance_klines,
) -> BinanceSyncResult:
    normalized_symbol = symbol.upper()
    instrument = binance_symbol_to_instrument(normalized_symbol)
    instrument_id = InstrumentRepository(connection).upsert(instrument)
    rows = fetcher(normalized_symbol, "1d", days)
    bars = [
        parse_binance_daily_kline(
            instrument_id=instrument_id,
            row=row,
            quote_currency=instrument.quote_currency,
            timezone_name=instrument.timezone,
        )
        for row in rows
    ]
    repository = DailyBarRepository(connection)
    for bar in bars:
        repository.upsert(bar)
    latest = bars[-1] if bars else None
    return BinanceSyncResult(
        symbol=normalized_symbol,
        interval="1d",
        bars=len(bars),
        latest_close=latest.close if latest else None,
    )


def parse_binance_daily_kline(
    *,
    instrument_id: int,
    row: list[object],
    quote_currency: str,
    timezone_name: str,
) -> DailyBar:
    if len(row) < 8:
        raise ValueError("Binance daily kline row must include at least 8 fields")
    timezone = ZoneInfo(timezone_name)
    trade_date = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC).astimezone(
        timezone
    ).date().isoformat()
    return DailyBar(
        instrument_id=instrument_id,
        trade_date=trade_date,
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume_raw=float(row[5]),
        turnover_raw=float(row[7]),
        quote_currency=quote_currency,
        source="binance",
    )


def parse_binance_kline(
    *,
    instrument_id: int,
    interval: str,
    row: list[object],
    now_ms: int,
    timezone_name: str,
) -> IntradayBar:
    if len(row) < 8:
        raise ValueError("Binance kline row must include at least 8 fields")
    open_time_ms = int(row[0])
    close_time_ms = int(row[6])
    timezone = ZoneInfo(timezone_name)
    local_start = datetime.fromtimestamp(open_time_ms / 1000, tz=UTC).astimezone(timezone)
    return IntradayBar(
        instrument_id=instrument_id,
        interval=interval,
        bar_start_ts_utc=_format_utc_ms(open_time_ms),
        bar_end_ts_utc=_format_utc_ms(close_time_ms + 1),
        trade_date_local=local_start.date().isoformat(),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume_raw=float(row[5]),
        turnover_raw=float(row[7]),
        is_closed_bar=close_time_ms < now_ms,
        source="binance",
    )


def binance_symbol_to_instrument(symbol: str) -> Instrument:
    normalized = symbol.upper()
    base_asset, quote_asset = _split_symbol(normalized)
    return Instrument(
        market="CRYPTO",
        symbol=normalized,
        display_name=f"{base_asset}/{quote_asset}",
        exchange="BINANCE",
        instrument_type="crypto",
        quote_currency=quote_asset,
        timezone="UTC",
        extra_meta={"base_asset": base_asset, "quote_asset": quote_asset},
    )


def _split_symbol(symbol: str) -> tuple[str, str]:
    for quote_asset in KNOWN_QUOTE_ASSETS:
        if symbol.endswith(quote_asset) and len(symbol) > len(quote_asset):
            return symbol[: -len(quote_asset)], quote_asset
    return symbol, "USD"


def _format_utc_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _change_pct(previous_close: float | None, latest_close: float | None) -> float | None:
    if previous_close in (None, 0) or latest_close is None:
        return None
    return ((latest_close - previous_close) / previous_close) * 100
