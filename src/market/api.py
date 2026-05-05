from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from market.alerts import ALERT_METRICS, CHART_INDICATORS
from market.binance import (
    InstrumentMetadataFetcher,
    RangeKlineFetcher,
    fetch_binance_klines_range,
    fetch_binance_symbol_trading_meta,
)
from market.binance_futures import (
    FuturesFundingFetcher,
    fetch_binance_futures_klines_range,
    fetch_binance_futures_premium_index,
    sync_binance_futures_daily_bars_range,
    sync_binance_futures_klines_range,
)
from market.collectors.alpaca import AlpacaCollector
from market.collectors.akshare import AkshareCollector
from market.crypto_gaps import (
    BINANCE_KLINES_MAX_LIMIT,
    fill_binance_1m_gaps,
    fill_binance_futures_1m_gaps,
)
from market.db import connect
from market.models import DailyBar, Instrument, IntradayBar
from market.repositories import (
    AlertEventRepository,
    AlertRuleRepository,
    DailyBarRepository,
    InstrumentRepository,
    IntradayBarRepository,
    RankingRepository,
)
from market.watchlists import sync_watchlist_from_file

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
REPO_ROOT = Path(__file__).resolve().parents[2]
FUTURES_MIN_HISTORY_BARS = 60
TRADEFI_BOARD_REFRESH_INTERVAL_SECONDS = 60
TRADEFI_BOARD_REFRESH_CONFIGS = {
    "A_SHARE_FOCUS20": {
        "provider": "akshare",
        "watchlist_config": REPO_ROOT / "config" / "watchlists" / "a_share_focus20.json",
        "watchlist_name": "A_SHARE_FOCUS20",
        "market": "A_SHARE",
        "instrument_type": "stock",
    },
    "HK_STOCK_FOCUS20": {
        "provider": "akshare",
        "watchlist_config": REPO_ROOT / "config" / "watchlists" / "hk_stock_focus20.json",
        "watchlist_name": "HK_STOCK_FOCUS20",
        "market": "HK",
        "instrument_type": "stock",
    },
    "US_STOCK_FOCUS20": {
        "provider": "alpaca",
        "watchlist_config": REPO_ROOT / "config" / "watchlists" / "us_stock_focus20.json",
        "watchlist_name": "US_STOCK_FOCUS20",
        "market": "US",
        "instrument_type": "stock",
    },
    "ETF_FOCUS20": {
        "provider": "alpaca",
        "watchlist_config": REPO_ROOT / "config" / "watchlists" / "etf_focus20.json",
        "watchlist_name": "ETF_FOCUS20",
        "market": "US",
        "instrument_type": "etf",
    },
    "INDEX_FOCUS20": {
        "provider": "akshare",
        "watchlist_config": REPO_ROOT / "config" / "watchlists" / "index_focus20.json",
        "watchlist_name": "INDEX_FOCUS20",
        "market": "US",
        "instrument_type": "index",
    },
    "COMMODITY_FOCUS20": {
        "provider": "akshare",
        "watchlist_config": REPO_ROOT / "config" / "watchlists" / "commodity_focus20.json",
        "watchlist_name": "COMMODITY_FOCUS20",
        "market": "CMDTY",
        "instrument_type": "commodity",
    },
}
_BOARD_REFRESH_LOCK = threading.Lock()
_BOARD_REFRESH_LAST_AT: dict[str, float] = {}
_BOARD_REFRESH_IN_FLIGHT: set[str] = set()
SSE_POLL_SECONDS = 1.0
SSE_HEARTBEAT_SECONDS = 20.0
SSE_MAX_CONNECTION_SECONDS = 30 * 60


@dataclass(frozen=True)
class StaticAsset:
    body: bytes
    content_type: str


def get_board_payload(
    connection: sqlite3.Connection,
    board_name: str,
    snapshot_ts_utc: str | None = None,
) -> dict[str, object]:
    resolved_snapshot = snapshot_ts_utc or _latest_snapshot_ts(connection, board_name)
    if resolved_snapshot is None:
        return {
            "board_name": board_name,
            "snapshot_ts_utc": None,
            "previous_snapshot_ts_utc": None,
            "items": [],
        }
    previous_snapshot = _previous_snapshot_ts(connection, board_name, resolved_snapshot)

    rows = connection.execute(
        """
        SELECT
            ranking_snapshot.rank,
            ranking_snapshot.turnover_raw,
            ranking_snapshot.quote_currency,
            ranking_snapshot.change_pct,
            ranking_snapshot.source,
            previous_ranking.rank AS previous_rank,
            market_snapshot.last_price,
            market_snapshot.volume_raw,
            market_snapshot.trade_date_local,
            previous_volume_snapshot.volume_raw AS previous_volume_raw,
            instrument.market,
            instrument.symbol,
            instrument.display_name,
            instrument.exchange,
            instrument.instrument_type,
            instrument.extra_meta
        FROM ranking_snapshot
        JOIN instrument
            ON instrument.instrument_id = ranking_snapshot.instrument_id
        LEFT JOIN market_snapshot
            ON market_snapshot.instrument_id = ranking_snapshot.instrument_id
            AND market_snapshot.snapshot_ts_utc = (
                SELECT max(latest_snapshot.snapshot_ts_utc)
                FROM market_snapshot AS latest_snapshot
                WHERE latest_snapshot.instrument_id = ranking_snapshot.instrument_id
            )
        LEFT JOIN market_snapshot AS previous_volume_snapshot
            ON previous_volume_snapshot.instrument_id = ranking_snapshot.instrument_id
            AND previous_volume_snapshot.trade_date_local = (
                SELECT max(previous_snapshot.trade_date_local)
                FROM market_snapshot AS previous_snapshot
                WHERE previous_snapshot.instrument_id = ranking_snapshot.instrument_id
                    AND previous_snapshot.trade_date_local < market_snapshot.trade_date_local
                    AND previous_snapshot.volume_raw IS NOT NULL
            )
        LEFT JOIN ranking_snapshot AS previous_ranking
            ON previous_ranking.board_name = ranking_snapshot.board_name
            AND previous_ranking.instrument_id = ranking_snapshot.instrument_id
            AND previous_ranking.snapshot_ts_utc = (
                SELECT max(previous_snapshot.snapshot_ts_utc)
                FROM ranking_snapshot AS previous_snapshot
                WHERE previous_snapshot.board_name = ranking_snapshot.board_name
                    AND previous_snapshot.snapshot_ts_utc < ranking_snapshot.snapshot_ts_utc
            )
        WHERE ranking_snapshot.board_name = ?
            AND ranking_snapshot.snapshot_ts_utc = ?
        ORDER BY ranking_snapshot.rank
        """,
        (board_name, resolved_snapshot),
    ).fetchall()
    return {
        "board_name": board_name,
        "snapshot_ts_utc": resolved_snapshot,
        "previous_snapshot_ts_utc": previous_snapshot,
        "items": [
            {
                "rank": rank,
                "previous_rank": previous_rank,
                "rank_change": None if previous_rank is None else previous_rank - rank,
                "market": str(row["market"]),
                "symbol": str(row["symbol"]),
                "display_name": str(row["display_name"]),
                "exchange": str(row["exchange"]),
                "instrument_type": str(row["instrument_type"]),
                "price_tick_size": _price_tick_size(row["extra_meta"]),
                "last_price": _optional_float(row["last_price"]),
                "volume_raw": _optional_float(row["volume_raw"]),
                "volume_change_pct": _percent_change(
                    _optional_float(row["previous_volume_raw"]),
                    _optional_float(row["volume_raw"]),
                ),
                "turnover_raw": float(row["turnover_raw"]),
                "quote_currency": str(row["quote_currency"]),
                "change_pct": _optional_float(row["change_pct"]),
                "source": str(row["source"]),
            }
            for row in rows
            for rank in [int(row["rank"])]
            for previous_rank in [_optional_int(row["previous_rank"])]
        ],
    }


def refresh_board_prices_on_open(
    connection: sqlite3.Connection,
    board_name: str,
    *,
    snapshot_ts_utc: str | None = None,
    akshare_collector_factory=None,
    alpaca_collector_factory=None,
) -> bool:
    config = TRADEFI_BOARD_REFRESH_CONFIGS.get(board_name)
    if config is None:
        return False
    sync_watchlist_from_file(connection, Path(config["watchlist_config"]))
    connection.commit()
    resolved_snapshot_ts = snapshot_ts_utc or _now_utc()
    watchlist_name = str(config["watchlist_name"])
    provider = str(config["provider"])
    if provider == "alpaca":
        alpaca_collector_factory = alpaca_collector_factory or AlpacaCollector
        alpaca_collector_factory().refresh_latest_prices(
            connection,
            watchlist_names=[watchlist_name],
            snapshot_ts_utc=resolved_snapshot_ts,
        )
        return True

    akshare_collector_factory = akshare_collector_factory or AkshareCollector
    result = akshare_collector_factory().sync_focus(
        connection,
        watchlist_names=[watchlist_name],
        days=365,
        snapshot_ts_utc=resolved_snapshot_ts,
        trade_date_local=None,
    )
    board_trade_date = _latest_watchlist_snapshot_trade_date(
        connection,
        watchlist_name=watchlist_name,
        market=str(config["market"]),
        instrument_type=str(config["instrument_type"]),
    )
    ranking_trade_date = (
        board_trade_date
        or result.metadata.get("trade_date_local")
        or datetime.now(tz=UTC).date().isoformat()
    )
    RankingRepository(connection).refresh_turnover_board(
        board_name=board_name,
        snapshot_ts_utc=resolved_snapshot_ts,
        trade_date_local=str(ranking_trade_date),
        market=str(config["market"]),
        instrument_type=str(config["instrument_type"]),
        limit=30,
        watchlist_name=watchlist_name,
    )
    connection.commit()
    return True


def schedule_board_prices_refresh_on_open(
    db_path: Path | str,
    board_name: str,
    *,
    min_interval_seconds: int = TRADEFI_BOARD_REFRESH_INTERVAL_SECONDS,
) -> bool:
    if board_name not in TRADEFI_BOARD_REFRESH_CONFIGS:
        return False
    if not _reserve_board_refresh(board_name, time.monotonic(), min_interval_seconds):
        return False

    thread = threading.Thread(
        target=_run_board_prices_refresh,
        args=(Path(db_path), board_name),
        daemon=True,
    )
    thread.start()
    return True


def _run_board_prices_refresh(db_path: Path, board_name: str) -> None:
    try:
        with connect(db_path) as connection:
            refresh_board_prices_on_open(connection, board_name)
    except Exception:
        return
    finally:
        _finish_board_refresh(board_name)


def _reserve_board_refresh(
    board_name: str,
    now_monotonic: float,
    min_interval_seconds: int,
) -> bool:
    with _BOARD_REFRESH_LOCK:
        if board_name in _BOARD_REFRESH_IN_FLIGHT:
            return False
        previous = _BOARD_REFRESH_LAST_AT.get(board_name)
        if previous is not None and now_monotonic - previous < min_interval_seconds:
            return False
        _BOARD_REFRESH_LAST_AT[board_name] = now_monotonic
        _BOARD_REFRESH_IN_FLIGHT.add(board_name)
        return True


def _finish_board_refresh(board_name: str) -> None:
    with _BOARD_REFRESH_LOCK:
        _BOARD_REFRESH_IN_FLIGHT.discard(board_name)


def get_instrument_payload(
    connection: sqlite3.Connection,
    market: str,
    symbol: str,
    *,
    instrument_metadata_fetcher: InstrumentMetadataFetcher = fetch_binance_symbol_trading_meta,
    include_funding: bool = False,
    futures_funding_fetcher: FuturesFundingFetcher = fetch_binance_futures_premium_index,
) -> dict[str, object] | None:
    instrument_repository = InstrumentRepository(connection)
    instrument = instrument_repository.get_by_market_symbol(market, symbol)
    if instrument is None or instrument.instrument_id is None:
        return None
    if market == "CRYPTO":
        instrument = _ensure_crypto_instrument_metadata(
            instrument_repository,
            instrument,
            instrument_metadata_fetcher,
        )

    snapshot = _latest_snapshot(connection, instrument.instrument_id)
    funding_rate = (
        _safe_futures_funding(symbol, futures_funding_fetcher)
        if include_funding and market == "CRYPTO_FUTURES"
        else None
    )
    return {
        "instrument_id": instrument.instrument_id,
        "market": instrument.market,
        "symbol": instrument.symbol,
        "display_name": instrument.display_name,
        "exchange": instrument.exchange,
        "instrument_type": instrument.instrument_type,
        "quote_currency": instrument.quote_currency,
        "timezone": instrument.timezone,
        "is_active": instrument.is_active,
        "extra_meta": instrument.extra_meta,
        "latest_snapshot": snapshot,
        "funding_rate": funding_rate,
    }


def _safe_futures_funding(
    symbol: str,
    funding_fetcher: FuturesFundingFetcher,
) -> dict[str, object] | None:
    try:
        return funding_fetcher(symbol.upper())
    except Exception:
        return None


def _ensure_crypto_instrument_metadata(
    instrument_repository: InstrumentRepository,
    instrument: Instrument,
    metadata_fetcher: InstrumentMetadataFetcher,
) -> Instrument:
    if instrument.extra_meta.get("price_tick_size"):
        return instrument
    try:
        metadata = metadata_fetcher(instrument.symbol)
    except Exception:
        return instrument
    if not metadata:
        return instrument
    updated = replace(
        instrument,
        extra_meta={**instrument.extra_meta, **metadata},
    )
    instrument_repository.upsert(updated)
    refreshed = instrument_repository.get_by_market_symbol(
        instrument.market,
        instrument.symbol,
    )
    return refreshed or updated


def get_daily_bars_payload(
    connection: sqlite3.Connection,
    market: str,
    symbol: str,
    *,
    before_trade_date: str | None = None,
    limit: int | None = None,
    now_ts_utc: str | None = None,
    futures_fetcher: RangeKlineFetcher = fetch_binance_futures_klines_range,
) -> dict[str, object]:
    instrument_repository = InstrumentRepository(connection)
    instrument = instrument_repository.get_by_market_symbol(market, symbol)
    resolved_limit = _clamp_limit(limit) if limit is not None else None
    if market == "CRYPTO_FUTURES" and (
        instrument is None or instrument.instrument_id is None
    ):
        _ensure_binance_futures_daily_window(
            connection,
            symbol=symbol,
            before_trade_date=before_trade_date,
            limit=resolved_limit or FUTURES_MIN_HISTORY_BARS,
            now_ts_utc=now_ts_utc,
            fetcher=futures_fetcher,
        )
        instrument = instrument_repository.get_by_market_symbol(market, symbol)
    if instrument is None or instrument.instrument_id is None:
        return {"market": market, "symbol": symbol, "interval": "1d", "items": []}

    bars = _list_daily_window(
        connection,
        instrument_id=instrument.instrument_id,
        before_trade_date=before_trade_date,
        limit=resolved_limit,
    )
    requested_limit = resolved_limit or FUTURES_MIN_HISTORY_BARS
    should_backfill = market == "CRYPTO_FUTURES" and (
        not bars
        or (before_trade_date is not None and len(bars) < requested_limit)
        or (before_trade_date is None and len(bars) < FUTURES_MIN_HISTORY_BARS)
    )
    if should_backfill:
        _ensure_binance_futures_daily_window(
            connection,
            symbol=symbol,
            before_trade_date=before_trade_date,
            limit=requested_limit,
            now_ts_utc=now_ts_utc,
            fetcher=futures_fetcher,
        )
        bars = _list_daily_window(
            connection,
            instrument_id=instrument.instrument_id,
            before_trade_date=before_trade_date,
            limit=resolved_limit,
        )
    return {
        "market": market,
        "symbol": symbol,
        "interval": "1d",
        "items": [
            {
                "interval": "1d",
                "trade_date": bar.trade_date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume_raw": bar.volume_raw,
                "turnover_raw": bar.turnover_raw,
                "quote_currency": bar.quote_currency,
                "source": bar.source,
            }
            for bar in bars
        ],
    }


def get_intraday_bars_payload(
    connection: sqlite3.Connection,
    market: str,
    symbol: str,
    interval: str,
    *,
    before_ts_utc: str | None = None,
    limit: int | None = None,
    now_ts_utc: str | None = None,
    gap_fetcher: RangeKlineFetcher | None = None,
    gap_min_request_interval_seconds: float = 1.0,
) -> dict[str, object]:
    instrument_repository = InstrumentRepository(connection)
    instrument = instrument_repository.get_by_market_symbol(market, symbol)
    backfilled_missing_instrument = False
    if instrument is None or instrument.instrument_id is None:
        if market in {"CRYPTO", "CRYPTO_FUTURES"}:
            _ensure_crypto_intraday_window(
                connection,
                market=market,
                symbol=symbol,
                interval=interval,
                before_ts_utc=before_ts_utc,
                now_ts_utc=now_ts_utc,
                fetcher=gap_fetcher,
                requested_limit=_clamp_limit(limit),
                min_request_interval_seconds=gap_min_request_interval_seconds,
            )
            backfilled_missing_instrument = True
            instrument = instrument_repository.get_by_market_symbol(market, symbol)
        if instrument is None or instrument.instrument_id is None:
            return {"market": market, "symbol": symbol, "interval": interval, "items": []}

    resolved_limit = _clamp_limit(limit)
    bars = _list_intraday_window(
        connection,
        instrument_id=instrument.instrument_id,
        interval=interval,
        before_ts_utc=before_ts_utc,
        limit=resolved_limit,
    )
    stale_latest_window = (
        market == "CRYPTO_FUTURES"
        and not backfilled_missing_instrument
        and before_ts_utc is None
        and bool(bars)
        and _latest_intraday_window_is_stale(
            bars[-1],
            interval=interval,
            now_ts_utc=now_ts_utc,
        )
    )
    short_futures_history = (
        market == "CRYPTO_FUTURES"
        and before_ts_utc is None
        and interval != "1m"
        and len(bars) < min(resolved_limit, FUTURES_MIN_HISTORY_BARS)
    )
    should_backfill = (
        not bars
        or stale_latest_window
        or short_futures_history
        or (before_ts_utc is not None and len(bars) < resolved_limit)
    )
    if market in {"CRYPTO", "CRYPTO_FUTURES"} and should_backfill:
        _ensure_crypto_intraday_window(
            connection,
            market=market,
            symbol=symbol,
            interval=interval,
            before_ts_utc=before_ts_utc,
            now_ts_utc=now_ts_utc,
            fetcher=gap_fetcher,
            requested_limit=resolved_limit,
            min_request_interval_seconds=gap_min_request_interval_seconds,
        )
        bars = _list_intraday_window(
            connection,
            instrument_id=instrument.instrument_id,
            interval=interval,
            before_ts_utc=before_ts_utc,
            limit=resolved_limit,
        )
    return {
        "market": market,
        "symbol": symbol,
        "interval": interval,
        "items": [
            {
                "interval": bar.interval,
                "bar_start_ts_utc": bar.bar_start_ts_utc,
                "bar_end_ts_utc": bar.bar_end_ts_utc,
                "trade_date_local": bar.trade_date_local,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume_raw": bar.volume_raw,
                "turnover_raw": bar.turnover_raw,
                "is_closed_bar": bar.is_closed_bar,
                "source": bar.source,
            }
            for bar in bars
        ],
    }


def _ensure_crypto_intraday_window(
    connection: sqlite3.Connection,
    *,
    market: str,
    symbol: str,
    interval: str,
    before_ts_utc: str | None,
    now_ts_utc: str | None,
    fetcher: RangeKlineFetcher | None,
    requested_limit: int,
    min_request_interval_seconds: float,
) -> None:
    minutes = _interval_minutes(interval)
    if minutes is None:
        return
    if market == "CRYPTO_FUTURES" and interval != "1m":
        _ensure_binance_futures_interval_window(
            connection,
            symbol=symbol,
            interval=interval,
            before_ts_utc=before_ts_utc,
            now_ts_utc=now_ts_utc,
            limit=max(requested_limit, FUTURES_MIN_HISTORY_BARS),
            fetcher=fetcher or fetch_binance_futures_klines_range,
        )
        return
    end = _parse_utc(before_ts_utc) if before_ts_utc else _resolve_now(now_ts_utc)
    if before_ts_utc is None and market == "CRYPTO_FUTURES":
        end += timedelta(minutes=1)
    start = end - timedelta(minutes=BINANCE_KLINES_MAX_LIMIT)
    if market == "CRYPTO_FUTURES":
        fill_func = fill_binance_futures_1m_gaps
        resolved_fetcher = fetcher or fetch_binance_futures_klines_range
    else:
        fill_func = fill_binance_1m_gaps
        resolved_fetcher = fetcher or fetch_binance_klines_range
    fill_func(
        connection,
        symbols=[symbol],
        start_ts_utc=_format_utc(start),
        end_ts_utc=_format_utc(end),
        fetcher=resolved_fetcher,
        min_request_interval_seconds=min_request_interval_seconds,
    )


def _ensure_binance_futures_interval_window(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    interval: str,
    before_ts_utc: str | None,
    now_ts_utc: str | None,
    limit: int,
    fetcher: RangeKlineFetcher,
) -> None:
    minutes = _interval_minutes(interval)
    if minutes is None:
        return
    if before_ts_utc is None:
        end = _next_interval_boundary(_resolve_now(now_ts_utc), minutes=minutes)
    else:
        end = _parse_utc(before_ts_utc)
    start = end - timedelta(minutes=minutes * limit)
    sync_binance_futures_klines_range(
        connection,
        symbol=symbol,
        interval=interval,
        start_time_ms=_to_ms(start),
        end_time_ms=_to_ms(end) - 1,
        limit=limit,
        fetcher=fetcher,
    )


def _ensure_binance_futures_daily_window(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    before_trade_date: str | None,
    limit: int,
    now_ts_utc: str | None,
    fetcher: RangeKlineFetcher,
) -> None:
    if before_trade_date is None:
        current_day = _resolve_now(now_ts_utc).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        end = current_day + timedelta(days=1)
    else:
        end = _parse_trade_date(before_trade_date)
    start = end - timedelta(days=limit)
    sync_binance_futures_daily_bars_range(
        connection,
        symbol=symbol,
        start_time_ms=_to_ms(start),
        end_time_ms=_to_ms(end) - 1,
        limit=limit,
        fetcher=fetcher,
    )


def _list_daily_window(
    connection: sqlite3.Connection,
    *,
    instrument_id: int,
    before_trade_date: str | None,
    limit: int | None,
) -> list[DailyBar]:
    if limit is None and before_trade_date is None:
        return DailyBarRepository(connection).list_for_instrument(instrument_id)
    params: list[object] = [instrument_id]
    before_filter = ""
    if before_trade_date is not None:
        before_filter = "AND trade_date < ?"
        params.append(_normalize_trade_date(before_trade_date))
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT ?"
        params.append(limit)
    rows = connection.execute(
        f"""
        SELECT
            instrument_id,
            trade_date,
            open,
            high,
            low,
            close,
            volume_raw,
            turnover_raw,
            quote_currency,
            source
        FROM bar_daily
        WHERE instrument_id = ?
            {before_filter}
        ORDER BY trade_date DESC
        {limit_clause}
        """,
        params,
    ).fetchall()
    return [_daily_bar_from_row(row) for row in reversed(rows)]


def _list_intraday_window(
    connection: sqlite3.Connection,
    *,
    instrument_id: int,
    interval: str,
    before_ts_utc: str | None,
    limit: int,
) -> list[IntradayBar]:
    params: list[object] = [instrument_id, interval]
    before_filter = ""
    if before_ts_utc is not None:
        before_filter = "AND bar_start_ts_utc < ?"
        params.append(_format_utc(_parse_utc(before_ts_utc)))
    params.append(limit)
    rows = connection.execute(
        f"""
        SELECT
            instrument_id,
            interval,
            bar_start_ts_utc,
            bar_end_ts_utc,
            trade_date_local,
            open,
            high,
            low,
            close,
            volume_raw,
            turnover_raw,
            is_closed_bar,
            source
        FROM bar_intraday
        WHERE instrument_id = ?
            AND interval = ?
            {before_filter}
        ORDER BY bar_start_ts_utc DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_intraday_bar_from_row(row) for row in reversed(rows)]


def _daily_bar_from_row(row: sqlite3.Row) -> DailyBar:
    return DailyBar(
        instrument_id=int(row["instrument_id"]),
        trade_date=str(row["trade_date"]),
        open=_optional_float(row["open"]),
        high=_optional_float(row["high"]),
        low=_optional_float(row["low"]),
        close=_optional_float(row["close"]),
        volume_raw=_optional_float(row["volume_raw"]),
        turnover_raw=_optional_float(row["turnover_raw"]),
        quote_currency=str(row["quote_currency"]),
        source=str(row["source"]),
    )


def _latest_intraday_window_is_stale(
    bar: IntradayBar,
    *,
    interval: str,
    now_ts_utc: str | None,
) -> bool:
    minutes = _interval_minutes(interval)
    if minutes is None:
        return False
    now = _resolve_now(now_ts_utc).replace(second=0, microsecond=0)
    bucket_minute = (now.hour * 60 + now.minute) // minutes * minutes
    current_bucket = now.replace(hour=bucket_minute // 60, minute=bucket_minute % 60)
    return _parse_utc(bar.bar_start_ts_utc) < current_bucket


def _intraday_bar_from_row(row: sqlite3.Row) -> IntradayBar:
    return IntradayBar(
        instrument_id=int(row["instrument_id"]),
        interval=str(row["interval"]),
        bar_start_ts_utc=str(row["bar_start_ts_utc"]),
        bar_end_ts_utc=str(row["bar_end_ts_utc"]),
        trade_date_local=str(row["trade_date_local"]),
        open=_optional_float(row["open"]),
        high=_optional_float(row["high"]),
        low=_optional_float(row["low"]),
        close=_optional_float(row["close"]),
        volume_raw=_optional_float(row["volume_raw"]),
        turnover_raw=_optional_float(row["turnover_raw"]),
        is_closed_bar=bool(row["is_closed_bar"]),
        source=str(row["source"]),
    )


def get_watchlists_payload(connection: sqlite3.Connection) -> dict[str, object]:
    rows = connection.execute(
        """
        SELECT
            watchlist.watchlist_name,
            watchlist.sort_order,
            watchlist.is_active,
            instrument.market,
            instrument.symbol,
            instrument.display_name,
            instrument.exchange,
            instrument.instrument_type,
            instrument.quote_currency,
            instrument.timezone
        FROM watchlist
        JOIN instrument
            ON instrument.instrument_id = watchlist.instrument_id
        WHERE watchlist.is_active = 1
            AND instrument.is_active = 1
        ORDER BY watchlist.watchlist_name, watchlist.sort_order, instrument.symbol
        """
    ).fetchall()
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["watchlist_name"]), []).append(
            {
                "sort_order": int(row["sort_order"]),
                "market": str(row["market"]),
                "symbol": str(row["symbol"]),
                "display_name": str(row["display_name"]),
                "exchange": str(row["exchange"]),
                "instrument_type": str(row["instrument_type"]),
                "quote_currency": str(row["quote_currency"]),
                "timezone": str(row["timezone"]),
            }
        )
    return {
        "watchlists": [
            {
                "watchlist_name": watchlist_name,
                "items": items,
            }
            for watchlist_name, items in grouped.items()
        ]
    }


def get_health_payload(connection: sqlite3.Connection) -> dict[str, object]:
    database_ok = _database_is_writable(connection)
    latest_boards = connection.execute(
        """
        SELECT
            latest.board_name,
            latest.snapshot_ts_utc,
            count(ranking_snapshot.rank) AS item_count
        FROM (
            SELECT board_name, max(snapshot_ts_utc) AS snapshot_ts_utc
            FROM ranking_snapshot
            GROUP BY board_name
        ) AS latest
        JOIN ranking_snapshot
            ON ranking_snapshot.board_name = latest.board_name
            AND ranking_snapshot.snapshot_ts_utc = latest.snapshot_ts_utc
        GROUP BY latest.board_name, latest.snapshot_ts_utc
        ORDER BY latest.board_name
        """
    ).fetchall()
    sources = connection.execute(
        """
        SELECT source_name, status, last_success_at, last_error_at, last_error
        FROM source_health
        ORDER BY source_name
        """
    ).fetchall()
    source_items = [_source_health_row(row) for row in sources]
    has_failed_source = any(item["status"] not in {"ok", "success"} for item in source_items)
    return {
        "status": "degraded" if has_failed_source or not database_ok else "ok",
        "database": {
            "writable": database_ok,
            "journal_mode": _journal_mode(connection),
        },
        "latest_boards": [
            {
                "board_name": str(row["board_name"]),
                "snapshot_ts_utc": str(row["snapshot_ts_utc"]),
                "item_count": int(row["item_count"]),
            }
            for row in latest_boards
        ],
        "sources": source_items,
    }


def get_jobs_payload(connection: sqlite3.Connection) -> dict[str, object]:
    job_rows = connection.execute(
        """
        SELECT
            job_name,
            checkpoint,
            status,
            last_started_at,
            last_finished_at,
            last_error,
            updated_at
        FROM job_state
        ORDER BY job_name
        """
    ).fetchall()
    source_rows = connection.execute(
        """
        SELECT source_name, status, last_success_at, last_error_at, last_error, updated_at
        FROM source_health
        ORDER BY source_name
        """
    ).fetchall()
    return {
        "jobs": [
            {
                "job_name": str(row["job_name"]),
                "checkpoint": _optional_str(row["checkpoint"]),
                "status": str(row["status"]),
                "last_started_at": _optional_str(row["last_started_at"]),
                "last_finished_at": _optional_str(row["last_finished_at"]),
                "last_error": _optional_str(row["last_error"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in job_rows
        ],
        "sources": [_source_health_row(row) for row in source_rows],
    }


def get_alert_rules_payload(connection: sqlite3.Connection) -> dict[str, object]:
    rules = AlertRuleRepository(connection).list_all()
    return {
        "rules": [
            {
                "rule_id": rule.rule_id,
                "name": rule.name,
                "market": rule.market,
                "symbol": rule.symbol,
                "metric": rule.metric,
                "operator": rule.operator,
                "threshold": rule.threshold,
                "is_active": rule.is_active,
            }
            for rule in rules
        ]
    }


def get_alert_metrics_payload() -> dict[str, object]:
    return {
        "metrics": list(ALERT_METRICS),
        "operators": [">", ">=", "<", "<=", "=="],
        "chart_indicators": list(CHART_INDICATORS),
    }


def get_alert_events_payload(
    connection: sqlite3.Connection,
    limit: int = 50,
) -> dict[str, object]:
    events = AlertEventRepository(connection).list_recent(limit=limit)
    return {
        "events": [
            {
                "event_id": event.event_id,
                "rule_id": event.rule_id,
                "rule_name": event.rule_name,
                "market": event.market,
                "symbol": event.symbol,
                "triggered_at_utc": event.triggered_at_utc,
                "metric": event.metric,
                "observed_value": event.observed_value,
                "threshold": event.threshold,
                "message": event.message,
                "is_acknowledged": event.is_acknowledged,
            }
            for event in events
        ]
    }


def register_mobile_device(
    connection: sqlite3.Connection,
    payload: dict[str, object],
    *,
    now_ts_utc: str | None = None,
) -> dict[str, object]:
    platform = _required_string(payload, "platform")
    getui_cid = _optional_payload_string(payload, "getui_cid")
    push_token = _optional_payload_string(payload, "push_token")
    device_label = _optional_payload_string(payload, "device_label")
    now = now_ts_utc or _now_utc()

    if platform not in {"android", "ios"}:
        raise ValueError("platform must be android or ios")
    if not push_token and not getui_cid:
        raise ValueError("push_token or getui_cid is required")
    if not push_token:
        push_token = f"getui:{getui_cid}"
    if getui_cid:
        connection.execute(
            """
            UPDATE push_device
            SET getui_cid = NULL, updated_at_utc = ?
            WHERE getui_cid = ?
                AND push_token != ?
            """,
            (now, getui_cid, push_token),
        )

    connection.execute(
        """
        INSERT INTO push_device (
            push_token,
            getui_cid,
            platform,
            device_label,
            enabled,
            created_at_utc,
            updated_at_utc
        )
        VALUES (?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(push_token) DO UPDATE SET
            getui_cid = COALESCE(excluded.getui_cid, push_device.getui_cid),
            platform = excluded.platform,
            device_label = excluded.device_label,
            enabled = 1,
            updated_at_utc = excluded.updated_at_utc
        """,
        (push_token, getui_cid, platform, device_label, now, now),
    )
    row = connection.execute(
        """
        SELECT push_device_id, platform, push_token, getui_cid, device_label, enabled
        FROM push_device
        WHERE push_token = ?
        """,
        (push_token,),
    ).fetchone()
    if row is None:
        raise ValueError("push device registration failed")
    return {
        "push_device_id": int(row["push_device_id"]),
        "platform": str(row["platform"]),
        "push_token": str(row["push_token"]),
        "getui_cid": _optional_str(row["getui_cid"]),
        "device_label": _optional_str(row["device_label"]),
        "enabled": bool(row["enabled"]),
    }


def create_mobile_alert_rule(
    connection: sqlite3.Connection,
    payload: dict[str, object],
    *,
    now_ts_utc: str | None = None,
) -> dict[str, object]:
    push_token = _required_string(payload, "push_token")
    market = _required_string(payload, "market")
    symbol = _required_string(payload, "symbol")
    source_type = _optional_payload_string(payload, "source_type") or "builtin"
    condition_type = _optional_payload_string(payload, "condition_type") or "custom_indicator"
    metric_key = _optional_payload_string(payload, "metric_key")
    operator_label = _optional_payload_string(payload, "operator")
    indicator_id = _optional_payload_int(payload, "indicator_id")
    threshold = _required_float(payload, "threshold")
    cooldown_seconds = _optional_payload_int(payload, "cooldown_seconds") or 900
    if source_type == "builtin":
        if condition_type not in {
            "price_above",
            "price_below",
            "change_pct_above",
            "change_pct_below",
        }:
            raise ValueError("invalid condition_type")
        metric_key = _metric_key_for_condition_type(condition_type)
        operator_label = _operator_for_condition_type(condition_type)
        indicator_id = None
    elif source_type == "custom_indicator":
        if indicator_id is None:
            raise ValueError("indicator_id required")
        if operator_label not in {">", ">=", "<", "<=", "=="}:
            raise ValueError("invalid operator")
        indicator_row = connection.execute(
            "SELECT indicator_id FROM indicator_definition WHERE indicator_id = ? AND enabled = 1",
            (indicator_id,),
        ).fetchone()
        if indicator_row is None:
            raise ValueError("indicator_id not found")
        metric_key = metric_key or "indicator_value"
    else:
        raise ValueError("invalid source_type")
    if cooldown_seconds < 0:
        raise ValueError("cooldown_seconds must be non-negative")

    push_device = _push_device_row_for_token(connection, push_token)
    if push_device is None:
        raise ValueError("push_token is not registered")
    now = now_ts_utc or _now_utc()
    connection.execute(
        """
        INSERT INTO mobile_alert_rule (
            push_device_id,
            symbol,
            market,
            condition_type,
            source_type,
            metric_key,
            operator,
            indicator_id,
            threshold,
            cooldown_seconds,
            enabled,
            created_at_utc,
            updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            int(push_device["push_device_id"]),
            symbol,
            market,
            condition_type,
            source_type,
            metric_key,
            operator_label,
            indicator_id,
            threshold,
            cooldown_seconds,
            now,
            now,
        ),
    )
    row = connection.execute(
        """
        SELECT *
        FROM mobile_alert_rule
        WHERE mobile_alert_rule_id = last_insert_rowid()
        """
    ).fetchone()
    if row is None:
        raise ValueError("mobile alert rule creation failed")
    return _mobile_alert_rule_payload(row)


def list_mobile_alert_rules(
    connection: sqlite3.Connection,
    *,
    push_token: str,
) -> dict[str, object]:
    push_device = _push_device_row_for_token(connection, push_token)
    if push_device is None:
        return {"rules": []}
    rows = connection.execute(
        """
        SELECT *
        FROM mobile_alert_rule
        WHERE push_device_id = ?
        ORDER BY created_at_utc DESC, mobile_alert_rule_id DESC
        """,
        (int(push_device["push_device_id"]),),
    ).fetchall()
    return {"rules": [_mobile_alert_rule_payload(row) for row in rows]}


def get_mobile_alert_events_payload(
    connection: sqlite3.Connection,
    *,
    push_token: str,
    after_id: int = 0,
    limit: int = 100,
) -> dict[str, object]:
    push_device = _push_device_row_for_token(connection, push_token)
    if push_device is None:
        return {"events": []}
    rows = connection.execute(
        """
        SELECT
            mobile_alert_event.mobile_alert_event_id,
            mobile_alert_event.triggered_at_utc,
            mobile_alert_event.observed_value,
            mobile_alert_event.message,
            mobile_alert_event.delivery_status,
            mobile_alert_rule.market,
            mobile_alert_rule.symbol,
            mobile_alert_rule.condition_type,
            mobile_alert_rule.threshold
        FROM mobile_alert_event
        JOIN mobile_alert_rule
            ON mobile_alert_rule.mobile_alert_rule_id = mobile_alert_event.mobile_alert_rule_id
        WHERE mobile_alert_rule.push_device_id = ?
            AND mobile_alert_event.mobile_alert_event_id > ?
        ORDER BY mobile_alert_event.mobile_alert_event_id
        LIMIT ?
        """,
        (int(push_device["push_device_id"]), after_id, limit),
    ).fetchall()
    return {"events": [_mobile_alert_event_payload(row) for row in rows]}


def get_mobile_push_device_debug_payload(
    connection: sqlite3.Connection,
    *,
    push_token: str,
) -> dict[str, object]:
    push_device = _push_device_row_for_token(connection, push_token)
    if push_device is None:
        return {
            "device": None,
            "checkpoint": None,
            "sessions": [],
            "recent_events": [],
            "recent_deliveries": [],
        }
    push_device_id = int(push_device["push_device_id"])
    checkpoint = connection.execute(
        """
        SELECT
            push_device_id,
            last_seen_mobile_alert_event_id,
            last_ack_mobile_alert_event_id,
            updated_at_utc
        FROM device_checkpoint
        WHERE push_device_id = ?
        """,
        (push_device_id,),
    ).fetchone()
    sessions = connection.execute(
        """
        SELECT session_id, transport, connected_at_utc, last_seen_at_utc, disconnected_at_utc
        FROM device_session
        WHERE push_device_id = ?
        ORDER BY last_seen_at_utc DESC
        LIMIT 5
        """,
        (push_device_id,),
    ).fetchall()
    event_rows = connection.execute(
        """
        SELECT
            mobile_alert_event.mobile_alert_event_id,
            mobile_alert_event.triggered_at_utc,
            mobile_alert_event.observed_value,
            mobile_alert_event.message,
            mobile_alert_event.delivery_status,
            mobile_alert_rule.market,
            mobile_alert_rule.symbol,
            mobile_alert_rule.condition_type,
            mobile_alert_rule.threshold
        FROM mobile_alert_event
        JOIN mobile_alert_rule
            ON mobile_alert_rule.mobile_alert_rule_id = mobile_alert_event.mobile_alert_rule_id
        WHERE mobile_alert_rule.push_device_id = ?
        ORDER BY mobile_alert_event.mobile_alert_event_id DESC
        LIMIT 10
        """,
        (push_device_id,),
    ).fetchall()
    return {
        "device": _push_device_payload(push_device),
        "checkpoint": (
            _device_checkpoint_payload(checkpoint) if checkpoint is not None else None
        ),
        "sessions": [_device_session_payload(row) for row in sessions],
        "recent_events": [_mobile_alert_event_payload(row) for row in event_rows],
        "recent_deliveries": get_mobile_delivery_debug_payload(
            connection,
            push_token=push_token,
            limit=10,
        )["deliveries"],
    }


def get_mobile_delivery_debug_payload(
    connection: sqlite3.Connection,
    *,
    push_token: str,
    limit: int = 50,
) -> dict[str, object]:
    push_device = _push_device_row_for_token(connection, push_token)
    if push_device is None:
        return {"device": None, "deliveries": []}
    push_device_id = int(push_device["push_device_id"])
    rows = connection.execute(
        """
        SELECT
            mobile_alert_delivery.mobile_alert_delivery_id,
            mobile_alert_delivery.mobile_alert_event_id,
            mobile_alert_delivery.push_device_id,
            mobile_alert_delivery.channel,
            mobile_alert_delivery.status,
            mobile_alert_delivery.attempt_count,
            mobile_alert_delivery.provider_message_id,
            mobile_alert_delivery.last_error,
            mobile_alert_delivery.created_at_utc,
            mobile_alert_delivery.updated_at_utc,
            mobile_alert_event.message,
            mobile_alert_event.delivery_status AS event_delivery_status,
            mobile_alert_rule.market,
            mobile_alert_rule.symbol,
            mobile_alert_rule.condition_type
        FROM mobile_alert_delivery
        JOIN mobile_alert_event
            ON mobile_alert_event.mobile_alert_event_id =
                mobile_alert_delivery.mobile_alert_event_id
        JOIN mobile_alert_rule
            ON mobile_alert_rule.mobile_alert_rule_id =
                mobile_alert_event.mobile_alert_rule_id
        WHERE mobile_alert_delivery.push_device_id = ?
        ORDER BY mobile_alert_delivery.updated_at_utc DESC,
            mobile_alert_delivery.mobile_alert_delivery_id DESC
        LIMIT ?
        """,
        (push_device_id, max(1, min(limit, 200))),
    ).fetchall()
    return {
        "device": _push_device_payload(push_device),
        "deliveries": [_mobile_delivery_debug_payload(row) for row in rows],
    }


def acknowledge_mobile_alert_events(
    connection: sqlite3.Connection,
    payload: dict[str, object],
    *,
    now_ts_utc: str | None = None,
) -> dict[str, object]:
    push_token = _required_string(payload, "push_token")
    last_seen = _optional_payload_int(payload, "last_seen_mobile_alert_event_id") or 0
    last_ack = _optional_payload_int(payload, "last_ack_mobile_alert_event_id") or last_seen
    push_device = _push_device_row_for_token(connection, push_token)
    if push_device is None:
        raise ValueError("push_token is not registered")
    now = now_ts_utc or _now_utc()
    connection.execute(
        """
        INSERT INTO device_checkpoint (
            push_device_id,
            last_seen_mobile_alert_event_id,
            last_ack_mobile_alert_event_id,
            updated_at_utc
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(push_device_id) DO UPDATE SET
            last_seen_mobile_alert_event_id = max(
                device_checkpoint.last_seen_mobile_alert_event_id,
                excluded.last_seen_mobile_alert_event_id
            ),
            last_ack_mobile_alert_event_id = max(
                device_checkpoint.last_ack_mobile_alert_event_id,
                excluded.last_ack_mobile_alert_event_id
            ),
            updated_at_utc = excluded.updated_at_utc
        """,
        (int(push_device["push_device_id"]), last_seen, last_ack, now),
    )
    row = connection.execute(
        """
        SELECT last_seen_mobile_alert_event_id, last_ack_mobile_alert_event_id, updated_at_utc
        FROM device_checkpoint
        WHERE push_device_id = ?
        """,
        (int(push_device["push_device_id"]),),
    ).fetchone()
    if row is None:
        raise ValueError("mobile alert checkpoint failed")
    return {
        "push_device_id": int(push_device["push_device_id"]),
        "last_seen_mobile_alert_event_id": int(row["last_seen_mobile_alert_event_id"]),
        "last_ack_mobile_alert_event_id": int(row["last_ack_mobile_alert_event_id"]),
        "updated_at_utc": str(row["updated_at_utc"]),
    }


def format_mobile_alert_sse_events(events: list[dict[str, object]]) -> bytes:
    chunks = []
    for event in events:
        event_id = int(event["mobile_alert_event_id"])
        chunks.append(
            "\n".join(
                [
                    "event: alert",
                    f"id: {event_id}",
                    f"data: {json.dumps(event, ensure_ascii=False)}",
                    "",
                    "",
                ]
            )
        )
    return "".join(chunks).encode("utf-8")


def mobile_alert_sse_heartbeat() -> bytes:
    return b": keep-alive\n\n"


def patch_mobile_alert_rule(
    connection: sqlite3.Connection,
    rule_id: int,
    payload: dict[str, object],
    *,
    now_ts_utc: str | None = None,
) -> dict[str, object]:
    if "enabled" not in payload:
        raise ValueError("enabled required")
    enabled = payload["enabled"]
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be boolean")
    now = now_ts_utc or _now_utc()
    connection.execute(
        """
        UPDATE mobile_alert_rule
        SET enabled = ?,
            updated_at_utc = ?
        WHERE mobile_alert_rule_id = ?
        """,
        (int(enabled), now, rule_id),
    )
    row = connection.execute(
        """
        SELECT *
        FROM mobile_alert_rule
        WHERE mobile_alert_rule_id = ?
        """,
        (rule_id,),
    ).fetchone()
    if row is None:
        raise ValueError("mobile alert rule not found")
    return _mobile_alert_rule_payload(row)


def get_static_asset(path: str) -> StaticAsset | None:
    asset_path = _asset_path(path)
    if asset_path is None:
        return None
    return StaticAsset(
        body=asset_path.read_bytes(),
        content_type=_content_type(asset_path),
    )


def serve_api(db_path: Path | str, host: str, port: int) -> None:
    handler = _make_handler(Path(db_path))
    server = ThreadingHTTPServer((host, port), handler)
    print(f"api listening: http://{host}:{port}")
    server.serve_forever()


def _make_handler(db_path: Path) -> type[BaseHTTPRequestHandler]:
    class MarketApiHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/mobile/devices":
                try:
                    request_payload = self._read_json_object()
                    with connect(db_path) as connection:
                        response_payload = register_mobile_device(connection, request_payload)
                except ValueError as error:
                    self._write_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                    return
                self._write_json(response_payload)
                return

            if parsed.path == "/api/mobile/alert-rules":
                try:
                    request_payload = self._read_json_object()
                    with connect(db_path) as connection:
                        response_payload = create_mobile_alert_rule(connection, request_payload)
                except ValueError as error:
                    self._write_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                    return
                self._write_json(response_payload)
                return

            if parsed.path == "/api/mobile/alert-events/ack":
                try:
                    request_payload = self._read_json_object()
                    with connect(db_path) as connection:
                        response_payload = acknowledge_mobile_alert_events(
                            connection,
                            request_payload,
                        )
                except ValueError as error:
                    self._write_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                    return
                self._write_json(response_payload)
                return

            self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_PATCH(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/mobile/alert-rules/"):
                try:
                    rule_id = int(parsed.path.removeprefix("/api/mobile/alert-rules/"))
                    request_payload = self._read_json_object()
                    with connect(db_path) as connection:
                        response_payload = patch_mobile_alert_rule(
                            connection,
                            rule_id,
                            request_payload,
                        )
                except ValueError as error:
                    self._write_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                    return
                self._write_json(response_payload)
                return

            self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/health":
                with connect(db_path) as connection:
                    payload = get_health_payload(connection)
                self._write_json(payload)
                return

            if parsed.path == "/api/watchlists":
                with connect(db_path) as connection:
                    payload = get_watchlists_payload(connection)
                self._write_json(payload)
                return

            if parsed.path == "/api/jobs":
                with connect(db_path) as connection:
                    payload = get_jobs_payload(connection)
                self._write_json(payload)
                return

            if parsed.path == "/api/alerts/rules":
                with connect(db_path) as connection:
                    payload = get_alert_rules_payload(connection)
                self._write_json(payload)
                return

            if parsed.path == "/api/alerts/metrics":
                self._write_json(get_alert_metrics_payload())
                return

            if parsed.path == "/api/alerts/events":
                query = parse_qs(parsed.query)
                limit_value = _first_query(query, "limit")
                limit = int(limit_value) if limit_value is not None else 50
                with connect(db_path) as connection:
                    payload = get_alert_events_payload(connection, limit=limit)
                self._write_json(payload)
                return

            if parsed.path == "/api/mobile/alert-rules":
                query = parse_qs(parsed.query)
                push_token = _first_query(query, "push_token")
                if push_token is None:
                    self._write_json({"error": "push_token required"}, HTTPStatus.BAD_REQUEST)
                    return
                with connect(db_path) as connection:
                    payload = list_mobile_alert_rules(connection, push_token=push_token)
                self._write_json(payload)
                return

            if parsed.path == "/api/mobile/alert-events":
                query = parse_qs(parsed.query)
                push_token = _first_query(query, "push_token")
                if push_token is None:
                    self._write_json({"error": "push_token required"}, HTTPStatus.BAD_REQUEST)
                    return
                after_id_value = _first_query(query, "after_id")
                limit_value = _first_query(query, "limit")
                after_id = int(after_id_value) if after_id_value is not None else 0
                limit = int(limit_value) if limit_value is not None else 100
                with connect(db_path) as connection:
                    payload = get_mobile_alert_events_payload(
                        connection,
                        push_token=push_token,
                        after_id=after_id,
                        limit=limit,
                    )
                self._write_json(payload)
                return

            if parsed.path == "/api/mobile/debug/push-device":
                query = parse_qs(parsed.query)
                push_token = _first_query(query, "push_token")
                if push_token is None:
                    self._write_json({"error": "push_token required"}, HTTPStatus.BAD_REQUEST)
                    return
                with connect(db_path) as connection:
                    payload = get_mobile_push_device_debug_payload(
                        connection,
                        push_token=push_token,
                    )
                self._write_json(payload)
                return

            if parsed.path == "/api/mobile/debug/deliveries":
                query = parse_qs(parsed.query)
                push_token = _first_query(query, "push_token")
                if push_token is None:
                    self._write_json({"error": "push_token required"}, HTTPStatus.BAD_REQUEST)
                    return
                limit = _optional_query_int(query, "limit") or 50
                with connect(db_path) as connection:
                    payload = get_mobile_delivery_debug_payload(
                        connection,
                        push_token=push_token,
                        limit=limit,
                    )
                self._write_json(payload)
                return

            if parsed.path == "/api/mobile/alert-stream":
                query = parse_qs(parsed.query)
                push_token = _first_query(query, "push_token")
                if push_token is None:
                    self._write_json({"error": "push_token required"}, HTTPStatus.BAD_REQUEST)
                    return
                after_id = _optional_query_int(query, "after_id") or 0
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self._stream_mobile_alert_events(
                    push_token=push_token,
                    after_id=after_id,
                )
                return

            if parsed.path.startswith("/api/boards/"):
                board_name = unquote(parsed.path.removeprefix("/api/boards/"))
                schedule_board_prices_refresh_on_open(db_path, board_name)
                with connect(db_path) as connection:
                    payload = get_board_payload(connection, board_name)
                self._write_json(payload)
                return

            if parsed.path.startswith("/api/instruments/"):
                parts = parsed.path.removeprefix("/api/instruments/").split("/", 1)
                if len(parts) != 2:
                    self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                    return
                market = unquote(parts[0])
                symbol = unquote(parts[1])
                query = parse_qs(parsed.query)
                include_funding = _first_query(query, "include_funding") == "1"
                with connect(db_path) as connection:
                    payload = get_instrument_payload(
                        connection,
                        market,
                        symbol,
                        include_funding=include_funding,
                    )
                if payload is None:
                    self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._write_json(payload)
                return

            if parsed.path == "/api/bars/daily":
                query = parse_qs(parsed.query)
                market = _first_query(query, "market")
                symbol = _first_query(query, "symbol")
                before_trade_date = _first_query(query, "before_trade_date")
                limit = _optional_query_int(query, "limit")
                if market is None or symbol is None:
                    self._write_json({"error": "market and symbol required"}, HTTPStatus.BAD_REQUEST)
                    return
                with connect(db_path) as connection:
                    payload = get_daily_bars_payload(
                        connection,
                        market,
                        symbol,
                        before_trade_date=before_trade_date,
                        limit=limit,
                    )
                self._write_json(payload)
                return

            if parsed.path == "/api/bars/intraday":
                query = parse_qs(parsed.query)
                market = _first_query(query, "market")
                symbol = _first_query(query, "symbol")
                interval = _first_query(query, "interval")
                before_ts_utc = _first_query(query, "before_ts_utc")
                limit = _optional_query_int(query, "limit")
                if market is None or symbol is None or interval is None:
                    self._write_json(
                        {"error": "market, symbol, and interval required"},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                with connect(db_path) as connection:
                    payload = get_intraday_bars_payload(
                        connection,
                        market,
                        symbol,
                        interval,
                        before_ts_utc=before_ts_utc,
                        limit=limit,
                    )
                self._write_json(payload)
                return

            asset = get_static_asset(parsed.path)
            if asset is not None:
                self._write_bytes(asset.body, asset.content_type)
                return

            self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _write_json(
            self,
            payload: dict[str, object],
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream_mobile_alert_events(self, *, push_token: str, after_id: int) -> None:
            session_id = uuid.uuid4().hex
            started = time.monotonic()
            last_heartbeat = 0.0
            current_after_id = after_id
            try:
                with connect(db_path) as connection:
                    push_device = _push_device_row_for_token(connection, push_token)
                    if push_device is None:
                        self.wfile.write(mobile_alert_sse_heartbeat())
                        self.wfile.flush()
                        return
                    push_device_id = int(push_device["push_device_id"])
                    _open_device_session(
                        connection,
                        session_id=session_id,
                        push_device_id=push_device_id,
                        transport="sse",
                        now_utc=_now_utc(),
                    )
                while time.monotonic() - started < SSE_MAX_CONNECTION_SECONDS:
                    with connect(db_path) as connection:
                        payload = get_mobile_alert_events_payload(
                            connection,
                            push_token=push_token,
                            after_id=current_after_id,
                            limit=100,
                        )
                        events = payload["events"] if isinstance(payload["events"], list) else []
                        if events:
                            self.wfile.write(format_mobile_alert_sse_events(events))
                            self.wfile.flush()
                            current_after_id = max(
                                int(event["mobile_alert_event_id"]) for event in events
                            )
                            _touch_device_session(
                                connection,
                                session_id=session_id,
                                now_utc=_now_utc(),
                            )
                            last_heartbeat = time.monotonic()
                        elif time.monotonic() - last_heartbeat >= SSE_HEARTBEAT_SECONDS:
                            self.wfile.write(mobile_alert_sse_heartbeat())
                            self.wfile.flush()
                            _touch_device_session(
                                connection,
                                session_id=session_id,
                                now_utc=_now_utc(),
                            )
                            last_heartbeat = time.monotonic()
                    time.sleep(SSE_POLL_SECONDS)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with connect(db_path) as connection:
                    _close_device_session(
                        connection,
                        session_id=session_id,
                        now_utc=_now_utc(),
                    )

        def _read_json_object(self) -> dict[str, object]:
            length = self.headers.get("Content-Length")
            if length is None:
                raise ValueError("Content-Length required")
            try:
                body_length = int(length)
            except ValueError as error:
                raise ValueError("invalid Content-Length") from error
            try:
                payload = json.loads(self.rfile.read(body_length).decode("utf-8"))
            except json.JSONDecodeError as error:
                raise ValueError("invalid JSON body") from error
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _write_bytes(
            self,
            body: bytes,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return MarketApiHandler


def _asset_path(path: str) -> Path | None:
    allowed = {
        "/": "index.html",
        "/index.html": "index.html",
        "/instrument.html": "instrument.html",
        "/status.html": "status.html",
        "/styles.css": "styles.css",
        "/app.js": "app.js",
        "/instrument.js": "instrument.js",
        "/status.js": "status.js",
        "/manifest.webmanifest": "manifest.webmanifest",
    }
    filename = allowed.get(path)
    if filename is None:
        return None
    candidate = FRONTEND_DIR / filename
    if not candidate.is_file():
        return None
    return candidate


def _content_type(path: Path) -> str:
    if path.suffix == ".html":
        return "text/html; charset=utf-8"
    if path.suffix == ".css":
        return "text/css; charset=utf-8"
    if path.suffix == ".js":
        return "text/javascript; charset=utf-8"
    if path.name == "manifest.webmanifest":
        return "application/manifest+json; charset=utf-8"
    return "application/octet-stream"


def _open_device_session(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    push_device_id: int,
    transport: str,
    now_utc: str,
) -> None:
    connection.execute(
        """
        INSERT INTO device_session (
            session_id,
            push_device_id,
            transport,
            connected_at_utc,
            last_seen_at_utc,
            disconnected_at_utc
        )
        VALUES (?, ?, ?, ?, ?, NULL)
        ON CONFLICT(session_id) DO UPDATE SET
            last_seen_at_utc = excluded.last_seen_at_utc,
            disconnected_at_utc = NULL
        """,
        (session_id, push_device_id, transport, now_utc, now_utc),
    )


def _touch_device_session(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    now_utc: str,
) -> None:
    connection.execute(
        """
        UPDATE device_session
        SET last_seen_at_utc = ?
        WHERE session_id = ?
        """,
        (now_utc, session_id),
    )


def _close_device_session(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    now_utc: str,
) -> None:
    connection.execute(
        """
        UPDATE device_session
        SET last_seen_at_utc = ?,
            disconnected_at_utc = ?
        WHERE session_id = ?
        """,
        (now_utc, now_utc, session_id),
    )


def _latest_snapshot_ts(connection: sqlite3.Connection, board_name: str) -> str | None:
    row = connection.execute(
        """
        SELECT snapshot_ts_utc
        FROM ranking_snapshot
        WHERE board_name = ?
        ORDER BY snapshot_ts_utc DESC
        LIMIT 1
        """,
        (board_name,),
    ).fetchone()
    if row is None:
        return None
    return str(row["snapshot_ts_utc"])


def _previous_snapshot_ts(
    connection: sqlite3.Connection,
    board_name: str,
    snapshot_ts_utc: str,
) -> str | None:
    row = connection.execute(
        """
        SELECT max(snapshot_ts_utc) AS snapshot_ts_utc
        FROM ranking_snapshot
        WHERE board_name = ?
            AND snapshot_ts_utc < ?
        """,
        (board_name, snapshot_ts_utc),
    ).fetchone()
    if row is None or row["snapshot_ts_utc"] is None:
        return None
    return str(row["snapshot_ts_utc"])


def _latest_watchlist_snapshot_trade_date(
    connection: sqlite3.Connection,
    *,
    watchlist_name: str,
    market: str,
    instrument_type: str,
) -> str | None:
    row = connection.execute(
        """
        SELECT max(market_snapshot.trade_date_local) AS trade_date_local
        FROM market_snapshot
        JOIN instrument
            ON instrument.instrument_id = market_snapshot.instrument_id
        JOIN watchlist
            ON watchlist.instrument_id = market_snapshot.instrument_id
            AND watchlist.watchlist_name = ?
            AND watchlist.is_active = 1
        WHERE instrument.market = ?
            AND instrument.instrument_type = ?
            AND instrument.is_active = 1
            AND market_snapshot.turnover_raw IS NOT NULL
        """,
        (watchlist_name, market, instrument_type),
    ).fetchone()
    if row is None or row["trade_date_local"] is None:
        return None
    return str(row["trade_date_local"])


def _latest_snapshot(
    connection: sqlite3.Connection,
    instrument_id: int,
) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT
            snapshot_ts_utc,
            trade_date_local,
            last_price,
            change_pct,
            volume_raw,
            turnover_raw,
            quote_currency,
            source
        FROM market_snapshot
        WHERE instrument_id = ?
        ORDER BY snapshot_ts_utc DESC
        LIMIT 1
        """,
        (instrument_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "snapshot_ts_utc": str(row["snapshot_ts_utc"]),
        "trade_date_local": str(row["trade_date_local"]),
        "last_price": _optional_float(row["last_price"]),
        "change_pct": _optional_float(row["change_pct"]),
        "volume_raw": _optional_float(row["volume_raw"]),
        "turnover_raw": _optional_float(row["turnover_raw"]),
        "quote_currency": str(row["quote_currency"]),
        "source": str(row["source"]),
    }


def _first_query(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values:
        return None
    return values[0]


def _required_string(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} required")
    return value.strip()


def _optional_payload_string(payload: dict[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    stripped = value.strip()
    return stripped or None


def _required_float(payload: dict[str, object], name: str) -> float:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    return float(value)


def _optional_payload_int(payload: dict[str, object], name: str) -> int | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _optional_query_int(query: dict[str, list[str]], name: str) -> int | None:
    value = _first_query(query, name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _clamp_limit(limit: int | None) -> int:
    if limit is None:
        return 500
    return max(1, min(limit, 500))


def _interval_minutes(interval: str) -> int | None:
    if interval.endswith("m"):
        return int(interval[:-1])
    if interval.endswith("h"):
        return int(interval[:-1]) * 60
    return None


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _normalize_trade_date(value: str) -> str:
    return datetime.fromisoformat(value).date().isoformat()


def _parse_trade_date(value: str) -> datetime:
    trade_date = datetime.fromisoformat(value).date()
    return datetime(
        trade_date.year,
        trade_date.month,
        trade_date.day,
        tzinfo=UTC,
    )


def _resolve_now(now_ts_utc: str | None) -> datetime:
    if now_ts_utc is not None:
        return _parse_utc(now_ts_utc)
    return datetime.now(UTC)


def _next_interval_boundary(value: datetime, *, minutes: int) -> datetime:
    interval_seconds = minutes * 60
    epoch_seconds = int(value.astimezone(UTC).timestamp())
    next_epoch = (epoch_seconds // interval_seconds + 1) * interval_seconds
    return datetime.fromtimestamp(next_epoch, tz=UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_ms(value: datetime) -> int:
    return int(value.astimezone(UTC).timestamp() * 1000)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _percent_change(previous: float | None, current: float | None) -> float | None:
    if previous in (None, 0) or current is None:
        return None
    return ((current - previous) / previous) * 100


def _price_tick_size(extra_meta: object) -> str | None:
    try:
        metadata = json.loads(str(extra_meta))
    except json.JSONDecodeError:
        return None
    tick_size = metadata.get("price_tick_size")
    return str(tick_size) if tick_size is not None else None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _now_utc() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _push_device_row_for_token(
    connection: sqlite3.Connection,
    push_token: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT push_device_id, push_token, getui_cid, platform, device_label, enabled
        FROM push_device
        WHERE push_token = ?
        """,
        (push_token,),
    ).fetchone()


def _push_device_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "push_device_id": int(row["push_device_id"]),
        "push_token": str(row["push_token"]),
        "getui_cid": _optional_str(row["getui_cid"]),
        "platform": str(row["platform"]),
        "device_label": _optional_str(row["device_label"]),
        "enabled": bool(row["enabled"]),
    }


def _device_checkpoint_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "push_device_id": int(row["push_device_id"]),
        "last_seen_mobile_alert_event_id": int(row["last_seen_mobile_alert_event_id"]),
        "last_ack_mobile_alert_event_id": int(row["last_ack_mobile_alert_event_id"]),
        "updated_at_utc": str(row["updated_at_utc"]),
    }


def _device_session_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "session_id": str(row["session_id"]),
        "transport": str(row["transport"]),
        "connected_at_utc": str(row["connected_at_utc"]),
        "last_seen_at_utc": str(row["last_seen_at_utc"]),
        "disconnected_at_utc": _optional_str(row["disconnected_at_utc"]),
    }


def _mobile_alert_rule_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "mobile_alert_rule_id": int(row["mobile_alert_rule_id"]),
        "push_device_id": int(row["push_device_id"]),
        "market": str(row["market"]),
        "symbol": str(row["symbol"]),
        "condition_type": str(row["condition_type"]),
        "source_type": str(row["source_type"]),
        "metric_key": _optional_str(row["metric_key"]),
        "operator": _optional_str(row["operator"]),
        "indicator_id": int(row["indicator_id"]) if row["indicator_id"] is not None else None,
        "created_by": str(row["created_by"]),
        "threshold": float(row["threshold"]),
        "cooldown_seconds": int(row["cooldown_seconds"]),
        "enabled": bool(row["enabled"]),
        "created_at_utc": str(row["created_at_utc"]),
        "updated_at_utc": str(row["updated_at_utc"]),
    }


def _mobile_alert_event_payload(row: sqlite3.Row) -> dict[str, object]:
    title_suffix = _mobile_alert_title_suffix(str(row["condition_type"]))
    return {
        "mobile_alert_event_id": int(row["mobile_alert_event_id"]),
        "market": str(row["market"]),
        "symbol": str(row["symbol"]),
        "title": f"{row['symbol']} {title_suffix}",
        "body": str(row["message"]),
        "triggered_at_utc": str(row["triggered_at_utc"]),
        "observed_value": float(row["observed_value"]),
        "threshold": float(row["threshold"]),
        "delivery_status": str(row["delivery_status"]),
        "data": {
            "market": str(row["market"]),
            "symbol": str(row["symbol"]),
            "url": f"/instrument.html?market={row['market']}&symbol={row['symbol']}",
        },
    }


def _mobile_delivery_debug_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "mobile_alert_delivery_id": int(row["mobile_alert_delivery_id"]),
        "mobile_alert_event_id": int(row["mobile_alert_event_id"]),
        "push_device_id": int(row["push_device_id"]),
        "channel": str(row["channel"]),
        "status": str(row["status"]),
        "attempt_count": int(row["attempt_count"]),
        "provider_message_id": _optional_str(row["provider_message_id"]),
        "last_error": _optional_str(row["last_error"]),
        "created_at_utc": str(row["created_at_utc"]),
        "updated_at_utc": str(row["updated_at_utc"]),
        "message": str(row["message"]),
        "event_delivery_status": str(row["event_delivery_status"]),
        "market": str(row["market"]),
        "symbol": str(row["symbol"]),
        "condition_type": str(row["condition_type"]),
    }


def _mobile_alert_title_suffix(condition_type: str) -> str:
    if condition_type.startswith("change_pct_"):
        return "涨跌幅提醒"
    return "价格提醒"


def _metric_key_for_condition_type(condition_type: str) -> str:
    if condition_type.startswith("change_pct_"):
        return "change_pct"
    return "last_price"


def _operator_for_condition_type(condition_type: str) -> str:
    return ">" if condition_type.endswith("_above") else "<"


def _database_is_writable(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError:
        return False
    return True


def _journal_mode(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA journal_mode").fetchone()
    if row is None:
        return "unknown"
    return str(row[0])


def _source_health_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "source_name": str(row["source_name"]),
        "status": str(row["status"]),
        "last_success_at": _optional_str(row["last_success_at"]),
        "last_error_at": _optional_str(row["last_error_at"]),
        "last_error": _optional_str(row["last_error"]),
        "updated_at": _optional_str(row["updated_at"]) if "updated_at" in row.keys() else None,
    }
