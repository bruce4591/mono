from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from market.models import DailyBar, Instrument, IntradayBar, MarketSnapshot
from market.repositories import (
    DailyBarRepository,
    InstrumentRepository,
    IntradayBarRepository,
    MarketSnapshotRepository,
)


@dataclass(frozen=True)
class SeedResult:
    instruments: int
    snapshots: int
    daily_bars: int
    intraday_bars: int


def seed_sample_data(
    connection: sqlite3.Connection,
    *,
    snapshot_ts_utc: str,
    trade_date_local: str,
) -> SeedResult:
    instruments = InstrumentRepository(connection)
    daily_bars = DailyBarRepository(connection)
    intraday_bars = IntradayBarRepository(connection)
    snapshots = MarketSnapshotRepository(connection)

    _delete_existing_sample_data(connection)

    rows = _sample_rows(snapshot_ts_utc=snapshot_ts_utc, trade_date_local=trade_date_local)
    daily_count = 0
    intraday_count = 0
    for row in rows:
        instrument_id = instruments.upsert(row["instrument"])
        for daily_bar in row["daily_bars"](instrument_id):
            daily_bars.upsert(daily_bar)
            daily_count += 1
        for intraday_bar in row["intraday_bars"](instrument_id):
            intraday_bars.upsert(intraday_bar)
            intraday_count += 1
        snapshots.upsert(row["snapshot"](instrument_id))

    return SeedResult(
        instruments=len(rows),
        snapshots=len(rows),
        daily_bars=daily_count,
        intraday_bars=intraday_count,
    )


def _delete_existing_sample_data(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM bar_intraday WHERE source = 'sample'")
    connection.execute("DELETE FROM bar_daily WHERE source = 'sample'")
    connection.execute("DELETE FROM market_snapshot WHERE source = 'sample'")


def _sample_rows(
    *,
    snapshot_ts_utc: str,
    trade_date_local: str,
) -> list[dict[str, object]]:
    return [
        _row(
            Instrument(
                market="US",
                symbol="SPY",
                display_name="SPDR S&P 500 ETF Trust",
                exchange="NYSEARCA",
                instrument_type="etf",
                quote_currency="USD",
                timezone="America/New_York",
            ),
            snapshot_ts_utc,
            trade_date_local,
            last_price=510.2,
            change_pct=0.82,
            volume=72_000_000.0,
            turnover=36_734_400_000.0,
            interval="60m",
        ),
        _row(
            Instrument(
                market="US",
                symbol="QQQ",
                display_name="Invesco QQQ Trust",
                exchange="NASDAQ",
                instrument_type="etf",
                quote_currency="USD",
                timezone="America/New_York",
            ),
            snapshot_ts_utc,
            trade_date_local,
            last_price=440.5,
            change_pct=1.21,
            volume=55_000_000.0,
            turnover=24_227_500_000.0,
            interval="60m",
        ),
        _row(
            Instrument(
                market="US",
                symbol="IWM",
                display_name="iShares Russell 2000 ETF",
                exchange="NYSEARCA",
                instrument_type="etf",
                quote_currency="USD",
                timezone="America/New_York",
            ),
            snapshot_ts_utc,
            trade_date_local,
            last_price=205.1,
            change_pct=-0.35,
            volume=24_000_000.0,
            turnover=4_922_400_000.0,
            interval="60m",
        ),
        _row(
            Instrument(
                market="CRYPTO",
                symbol="BTCUSDT",
                display_name="Bitcoin / Tether",
                exchange="BINANCE",
                instrument_type="crypto",
                quote_currency="USDT",
                timezone="UTC",
            ),
            snapshot_ts_utc,
            trade_date_local,
            last_price=64_250.0,
            change_pct=2.14,
            volume=42_000.0,
            turnover=2_698_500_000.0,
            interval="15m",
        ),
        _row(
            Instrument(
                market="CRYPTO",
                symbol="ETHUSDT",
                display_name="Ether / Tether",
                exchange="BINANCE",
                instrument_type="crypto",
                quote_currency="USDT",
                timezone="UTC",
            ),
            snapshot_ts_utc,
            trade_date_local,
            last_price=3_180.0,
            change_pct=1.76,
            volume=380_000.0,
            turnover=1_208_400_000.0,
            interval="15m",
        ),
    ]


def _row(
    instrument: Instrument,
    snapshot_ts_utc: str,
    trade_date_local: str,
    *,
    last_price: float,
    change_pct: float,
    volume: float,
    turnover: float,
    interval: str,
) -> dict[str, object]:
    return {
        "instrument": instrument,
        "daily_bars": lambda instrument_id: _daily_bars(
            instrument_id=instrument_id,
            trade_date_local=trade_date_local,
            last_price=last_price,
            volume=volume,
            turnover=turnover,
            quote_currency=instrument.quote_currency,
        ),
        "intraday_bars": lambda instrument_id: _intraday_bars(
            instrument_id=instrument_id,
            interval=interval,
            trade_date_local=trade_date_local,
            last_price=last_price,
            volume=volume,
            turnover=turnover,
        ),
        "snapshot": lambda instrument_id: MarketSnapshot(
            instrument_id=instrument_id,
            snapshot_ts_utc=snapshot_ts_utc,
            trade_date_local=trade_date_local,
            last_price=last_price,
            change_pct=change_pct,
            volume_raw=volume,
            turnover_raw=turnover,
            quote_currency=instrument.quote_currency,
            source="sample",
        ),
    }


def _daily_bars(
    *,
    instrument_id: int,
    trade_date_local: str,
    last_price: float,
    volume: float,
    turnover: float,
    quote_currency: str,
) -> list[DailyBar]:
    trade_date = datetime.strptime(trade_date_local, "%Y-%m-%d").date()
    bars: list[DailyBar] = []
    for index in range(5):
        factor = 1 + (index - 4) * 0.006
        close = last_price * factor
        open_price = close * (0.996 if index % 2 == 0 else 1.004)
        high = max(open_price, close) * 1.006
        low = min(open_price, close) * 0.994
        volume_factor = 0.82 + index * 0.045
        bars.append(
            DailyBar(
                instrument_id=instrument_id,
                trade_date=(trade_date - timedelta(days=4 - index)).isoformat(),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume_raw=volume * volume_factor,
                turnover_raw=turnover * volume_factor,
                quote_currency=quote_currency,
                source="sample",
            )
        )
    return bars


def _intraday_bars(
    *,
    instrument_id: int,
    interval: str,
    trade_date_local: str,
    last_price: float,
    volume: float,
    turnover: float,
) -> list[IntradayBar]:
    step = timedelta(minutes=60 if interval == "60m" else 15)
    start = datetime.fromisoformat("2026-04-24T14:00:00+00:00")
    bars: list[IntradayBar] = []
    for index in range(6):
        factor = 1 + (index - 5) * 0.0028
        close = last_price * factor
        open_price = close * (0.998 if index % 2 == 0 else 1.002)
        high = max(open_price, close) * 1.003
        low = min(open_price, close) * 0.997
        bar_start = start + step * index
        bar_end = bar_start + step
        volume_factor = 0.12 + index * 0.018
        bars.append(
            IntradayBar(
                instrument_id=instrument_id,
                interval=interval,
                bar_start_ts_utc=bar_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                bar_end_ts_utc=bar_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                trade_date_local=trade_date_local,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume_raw=volume * volume_factor,
                turnover_raw=turnover * volume_factor,
                is_closed_bar=True,
                source="sample",
            )
        )
    return bars
