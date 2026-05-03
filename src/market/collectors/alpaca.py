from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from market.collectors.base import CollectorResult
from market.models import DailyBar, Instrument, MarketSnapshot
from market.repositories import DailyBarRepository, MarketSnapshotRepository, WatchlistRepository


ALPACA_DATA_BASE_URL = "https://data.alpaca.markets/v2"
ALPACA_DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0

AlpacaBarsFetcher = Callable[[list[str], str, str, float], dict[str, list[dict[str, object]]]]


class AlpacaCollector:
    source_name = "alpaca"

    def __init__(
        self,
        *,
        api_key_id: str | None = None,
        api_secret_key: str | None = None,
        data_base_url: str = ALPACA_DATA_BASE_URL,
        bars_fetcher: AlpacaBarsFetcher | None = None,
        request_timeout_seconds: float = ALPACA_DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key_id = api_key_id or os.environ.get("ALPACA_API_KEY_ID")
        self.api_secret_key = api_secret_key or os.environ.get("ALPACA_API_SECRET_KEY")
        self.data_base_url = data_base_url.rstrip("/")
        self.bars_fetcher = bars_fetcher or self._fetch_daily_bars
        self.request_timeout_seconds = request_timeout_seconds

    def sync_universe(self, connection: sqlite3.Connection) -> CollectorResult:
        return CollectorResult(source_name=self.source_name, items_synced=0)

    def sync_daily_bars(
        self,
        connection: sqlite3.Connection,
        symbols: list[str],
        days: int,
    ) -> CollectorResult:
        instruments = [
            instrument
            for symbol in symbols
            for instrument in [_instrument_by_market_symbol(connection, "US", symbol)]
            if instrument is not None
        ]
        return self._sync_instruments(
            connection,
            instruments=instruments,
            days=days,
            snapshot_ts_utc=_now_utc(),
            trade_date_local=None,
            metadata={"symbols": [instrument.symbol for instrument in instruments]},
        )

    def sync_intraday_bars(
        self,
        connection: sqlite3.Connection,
        symbols: list[str],
        interval: str,
        limit: int,
    ) -> CollectorResult:
        return CollectorResult(
            source_name=self.source_name,
            items_synced=0,
            metadata={"symbols": [symbol.upper() for symbol in symbols], "interval": interval},
        )

    def sync_snapshots(
        self,
        connection: sqlite3.Connection,
        symbols: list[str],
    ) -> CollectorResult:
        return self.sync_daily_bars(connection, symbols, days=2)

    def sync_focus(
        self,
        connection: sqlite3.Connection,
        *,
        watchlist_names: list[str],
        days: int,
        snapshot_ts_utc: str,
        trade_date_local: str | None,
    ) -> CollectorResult:
        instruments = _focus_instruments(connection, watchlist_names)
        return self._sync_instruments(
            connection,
            instruments=instruments,
            days=days,
            snapshot_ts_utc=snapshot_ts_utc,
            trade_date_local=trade_date_local,
            metadata={"watchlists": watchlist_names},
        )

    def _sync_instruments(
        self,
        connection: sqlite3.Connection,
        *,
        instruments: list[Instrument],
        days: int,
        snapshot_ts_utc: str,
        trade_date_local: str | None,
        metadata: dict[str, object],
    ) -> CollectorResult:
        daily_repository = DailyBarRepository(connection)
        snapshot_repository = MarketSnapshotRepository(connection)
        symbols = [instrument.symbol for instrument in instruments if instrument.instrument_id is not None]
        if not symbols:
            return CollectorResult(source_name=self.source_name, items_synced=0, metadata=metadata)

        end = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
        start = (datetime.now(tz=UTC).date() - timedelta(days=max(days * 2, 7))).isoformat()
        bars_by_symbol = self.bars_fetcher(symbols, start, end, self.request_timeout_seconds)

        bars_synced = 0
        snapshot_trade_dates: list[str] = []
        failed_symbols: list[str] = []
        for instrument in instruments:
            if instrument.instrument_id is None:
                continue
            payload_bars = bars_by_symbol.get(instrument.symbol, [])
            if not payload_bars:
                failed_symbols.append(f"{instrument.market}:{instrument.symbol}")
                continue
            bars = [
                parse_alpaca_daily_bar(
                    instrument_id=instrument.instrument_id,
                    payload=payload,
                    quote_currency=instrument.quote_currency,
                    source=self.source_name,
                )
                for payload in payload_bars
            ]
            selected_bars = sorted(bars, key=lambda bar: bar.trade_date)[-days:]
            for bar in selected_bars:
                daily_repository.upsert(bar)
            bars_synced += len(selected_bars)
            snapshot = _snapshot_from_bars(
                instrument=instrument,
                bars=selected_bars,
                snapshot_ts_utc=snapshot_ts_utc,
                trade_date_local=trade_date_local,
                source=self.source_name,
            )
            if snapshot is not None:
                snapshot_repository.upsert(snapshot)
                snapshot_trade_dates.append(snapshot.trade_date_local)
        connection.commit()

        resolved_trade_date = trade_date_local or (max(snapshot_trade_dates) if snapshot_trade_dates else None)
        return CollectorResult(
            source_name=self.source_name,
            items_synced=bars_synced,
            metadata={
                **metadata,
                "days": days,
                "trade_date_local": resolved_trade_date,
                "failed_symbols": failed_symbols,
                "request_timeout_seconds": self.request_timeout_seconds,
            },
        )

    def _fetch_daily_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
        timeout: float,
    ) -> dict[str, list[dict[str, object]]]:
        if not self.api_key_id or not self.api_secret_key:
            raise RuntimeError("ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY are required")

        bars: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in symbols}
        page_token: str | None = None
        while True:
            params = {
                "symbols": ",".join(symbols),
                "timeframe": "1Day",
                "start": f"{start}T00:00:00Z",
                "end": f"{end}T00:00:00Z",
                "limit": "10000",
            }
            if page_token:
                params["page_token"] = page_token
            request = Request(
                f"{self.data_base_url}/stocks/bars?{urlencode(params)}",
                headers={
                    "APCA-API-KEY-ID": self.api_key_id,
                    "APCA-API-SECRET-KEY": self.api_secret_key,
                },
            )
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            for symbol, symbol_bars in payload.get("bars", {}).items():
                bars.setdefault(symbol, []).extend(symbol_bars)
            page_token = payload.get("next_page_token")
            if not page_token:
                return bars


def parse_alpaca_daily_bar(
    *,
    instrument_id: int,
    payload: dict[str, object],
    quote_currency: str,
    source: str,
) -> DailyBar:
    close = _optional_float(payload.get("c"))
    volume = _optional_float(payload.get("v"))
    return DailyBar(
        instrument_id=instrument_id,
        trade_date=str(payload.get("t"))[:10],
        open=_optional_float(payload.get("o")),
        high=_optional_float(payload.get("h")),
        low=_optional_float(payload.get("l")),
        close=close,
        volume_raw=volume,
        turnover_raw=close * volume if close is not None and volume is not None else None,
        quote_currency=quote_currency,
        source=source,
    )


def _snapshot_from_bars(
    *,
    instrument: Instrument,
    bars: list[DailyBar],
    snapshot_ts_utc: str,
    trade_date_local: str | None,
    source: str,
) -> MarketSnapshot | None:
    if instrument.instrument_id is None or not bars:
        return None
    latest = bars[-1]
    previous = bars[-2] if len(bars) >= 2 else None
    return MarketSnapshot(
        instrument_id=instrument.instrument_id,
        snapshot_ts_utc=snapshot_ts_utc,
        trade_date_local=trade_date_local or latest.trade_date,
        last_price=latest.close,
        change_pct=_percent_change(previous.close if previous else None, latest.close),
        volume_raw=latest.volume_raw,
        turnover_raw=latest.turnover_raw,
        quote_currency=instrument.quote_currency,
        source=source,
    )


def _focus_instruments(
    connection: sqlite3.Connection,
    watchlist_names: list[str],
) -> list[Instrument]:
    seen: set[int] = set()
    instruments: list[Instrument] = []
    watchlists = WatchlistRepository(connection)
    for watchlist_name in watchlist_names:
        for entry in watchlists.list_active(watchlist_name):
            if entry.instrument_id in seen:
                continue
            instrument = _instrument_by_id(connection, entry.instrument_id)
            if instrument is None:
                continue
            seen.add(entry.instrument_id)
            instruments.append(instrument)
    return instruments


def _instrument_by_id(connection: sqlite3.Connection, instrument_id: int) -> Instrument | None:
    row = connection.execute(
        """
        SELECT
            instrument_id,
            market,
            symbol,
            display_name,
            exchange,
            instrument_type,
            quote_currency,
            timezone,
            is_active,
            extra_meta
        FROM instrument
        WHERE instrument_id = ?
        """,
        (instrument_id,),
    ).fetchone()
    if row is None:
        return None
    return Instrument(
        instrument_id=int(row["instrument_id"]),
        market=str(row["market"]),
        symbol=str(row["symbol"]),
        display_name=str(row["display_name"]),
        exchange=str(row["exchange"]),
        instrument_type=str(row["instrument_type"]),
        quote_currency=str(row["quote_currency"]),
        timezone=str(row["timezone"]),
        is_active=bool(row["is_active"]),
        extra_meta=json.loads(str(row["extra_meta"])),
    )


def _instrument_by_market_symbol(
    connection: sqlite3.Connection,
    market: str,
    symbol: str,
) -> Instrument | None:
    row = connection.execute(
        "SELECT instrument_id FROM instrument WHERE market = ? AND symbol = ?",
        (market, symbol.upper()),
    ).fetchone()
    if row is None:
        return None
    return _instrument_by_id(connection, int(row["instrument_id"]))


def _optional_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent_change(previous: float | None, current: float | None) -> float | None:
    if previous in (None, 0) or current is None:
        return None
    return ((current - previous) / previous) * 100


def _now_utc() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
