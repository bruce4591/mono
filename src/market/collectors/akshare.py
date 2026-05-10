from __future__ import annotations

import math
import signal
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime
from time import sleep as default_sleep
from typing import Callable

from market.collectors.base import CollectorResult, RequestRateLimiter
from market.models import DailyBar, Instrument, MarketSnapshot
from market.repositories import DailyBarRepository, MarketSnapshotRepository, WatchlistRepository


AKSHARE_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS = 1.0
AKSHARE_DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
AKSHARE_INDEX_SYMBOLS = {
    "SPX": ".INX",
    "NDX": ".NDX",
    "DJI": ".DJI",
}

AkshareDailyFetcher = Callable[[Instrument], object]


class AkshareCollector:
    source_name = "akshare"

    def __init__(
        self,
        *,
        daily_fetcher: AkshareDailyFetcher | None = None,
        min_request_interval_seconds: float = AKSHARE_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
        request_timeout_seconds: float = AKSHARE_DEFAULT_REQUEST_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = default_sleep,
    ) -> None:
        self.daily_fetcher = daily_fetcher or fetch_akshare_daily_frame
        self.min_request_interval_seconds = min_request_interval_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.rate_limiter = RequestRateLimiter(
            min_request_interval_seconds,
            sleep=sleep,
        )

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
        instruments = [
            instrument
            for symbol in symbols
            for instrument in [_instrument_by_market_symbol(connection, "US", symbol)]
            if instrument is not None
        ]
        return self._sync_instruments(
            connection,
            instruments=instruments,
            days=2,
            snapshot_ts_utc=_now_utc(),
            trade_date_local=None,
            metadata={"symbols": [instrument.symbol for instrument in instruments]},
        )

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
        bars_synced = 0
        snapshot_trade_dates: list[str] = []
        failed_symbols: list[str] = []
        for instrument in instruments:
            if instrument.instrument_id is None:
                continue
            self.rate_limiter.wait()
            try:
                bars = parse_akshare_daily_frame(
                    instrument_id=instrument.instrument_id,
                    frame=self._fetch_daily_frame(instrument),
                    quote_currency=instrument.quote_currency,
                    source=self.source_name,
                )
            except Exception:
                failed_symbols.append(f"{instrument.market}:{instrument.symbol}")
                continue
            selected_bars = bars[-days:]
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
                "min_request_interval_seconds": self.min_request_interval_seconds,
                "request_timeout_seconds": self.request_timeout_seconds,
            },
        )

    def _fetch_daily_frame(self, instrument: Instrument):
        with _request_timeout(self.request_timeout_seconds, instrument):
            return self.daily_fetcher(instrument)


def fetch_akshare_daily_frame(instrument: Instrument):
    akshare = _load_akshare()
    if instrument.instrument_type == "index":
        if instrument.extra_meta.get("akshare_function") == "index_global_hist_em":
            return akshare.index_global_hist_em(
                symbol=str(instrument.extra_meta.get("akshare_symbol") or instrument.display_name)
            )
        return akshare.index_us_stock_sina(
            symbol=AKSHARE_INDEX_SYMBOLS.get(instrument.symbol.upper(), instrument.symbol)
        )
    if instrument.instrument_type == "commodity":
        if instrument.extra_meta.get("akshare_function") == "futures_global_hist_em":
            return akshare.futures_global_hist_em(symbol=instrument.symbol.upper())
        return akshare.futures_foreign_hist(symbol=instrument.symbol.upper())
    if instrument.market == "A_SHARE":
        return akshare.stock_zh_a_hist(
            symbol=instrument.symbol.upper(),
            period="daily",
            adjust="",
        )
    if instrument.market == "HK":
        return akshare.stock_hk_hist(
            symbol=instrument.symbol.upper(),
            period="daily",
            adjust="",
        )
    return akshare.stock_us_daily(symbol=instrument.symbol.upper(), adjust="")


def parse_akshare_daily_frame(
    *,
    instrument_id: int,
    frame,
    quote_currency: str,
    source: str,
) -> list[DailyBar]:
    bars = [
        DailyBar(
            instrument_id=instrument_id,
            trade_date=_normalize_trade_date(_record_value(record, "date", "日期")),
            open=_optional_float(_record_value(record, "open", "开盘", "今开")),
            high=_optional_float(_record_value(record, "high", "最高")),
            low=_optional_float(_record_value(record, "low", "最低")),
            close=_optional_float(_record_value(record, "close", "收盘", "最新价")),
            volume_raw=_optional_float(_record_value(record, "volume", "成交量", "总量")),
            turnover_raw=_turnover_from_record(record),
            quote_currency=quote_currency,
            source=source,
        )
        for record in _records_from_frame(frame)
    ]
    return sorted(
        [bar for bar in bars if bar.trade_date],
        key=lambda bar: bar.trade_date,
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
        turnover_raw=_snapshot_turnover(instrument, latest),
        quote_currency=instrument.quote_currency,
        source=source,
    )


def _snapshot_turnover(instrument: Instrument, latest: DailyBar) -> float | None:
    if latest.turnover_raw is not None:
        return latest.turnover_raw
    if instrument.instrument_type == "index":
        return 0.0
    return None


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
        extra_meta=_json_object(row["extra_meta"]),
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


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    import json

    return json.loads(str(value))


def _records_from_frame(frame) -> list[dict[str, object]]:
    records = frame.to_dict("records")
    if records and any("date" in record or "日期" in record for record in records):
        return records
    if hasattr(frame, "reset_index"):
        return frame.reset_index().to_dict("records")
    return records


def _record_value(record: dict[str, object], *names: str):
    for name in names:
        if name in record:
            return record[name]
    return None


def _turnover_from_record(record: dict[str, object]) -> float | None:
    amount = _optional_float(_record_value(record, "amount", "成交额"))
    if amount not in (None, 0):
        return amount
    close = _optional_float(_record_value(record, "close", "收盘", "最新价"))
    volume = _optional_float(_record_value(record, "volume", "成交量", "总量"))
    if close is None or volume is None:
        return None
    return close * volume


def _normalize_trade_date(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


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


def _now_utc() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _request_timeout(timeout_seconds: float, instrument: Instrument):
    if timeout_seconds <= 0:
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)

    def handle_timeout(signum, frame):
        raise TimeoutError(
            f"akshare request timed out after {timeout_seconds:g}s: "
            f"{instrument.market}:{instrument.symbol}"
        )

    signal.signal(signal.SIGALRM, handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def _load_akshare():
    try:
        import akshare
    except ImportError as error:
        raise RuntimeError("akshare dependency is required for sync-akshare-focus") from error
    return akshare
