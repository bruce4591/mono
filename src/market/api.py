from __future__ import annotations

import gzip
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from market.alerts import (
    ALERT_METRICS,
    CHART_INDICATORS,
    PIN_PAPER_STRATEGY_ID,
    PIN_PAPER_STRATEGY_MARKET,
    PIN_PAPER_STRATEGY_SYMBOLS,
)
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
from market.db import connect, create_database_connector
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


MOBILE_HOME_BOARDS = (
    ("ETF_FOCUS20", "ETF", "US"),
    ("HK_STOCK_FOCUS20", "HK", "HK"),
    ("US_STOCK_FOCUS20", "US", "US"),
    ("A_SHARE_FOCUS20", "A-SH", "A_SHARE"),
    ("INDEX_FOCUS20", "IDX", "INDEX"),
    ("COMMODITY_FOCUS20", "CMDTY", "CMDTY"),
    ("CRYPTO_TURNOVER_TOP50", "Crypto", "CRYPTO"),
    ("CRYPTO_FUTURES_TURNOVER_TOP50", "Futures", "CRYPTO_FUTURES"),
    ("CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50", "TradeFi", "CRYPTO_FUTURES"),
)


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
        "snapshot_ts_utc": _api_str(resolved_snapshot),
        "previous_snapshot_ts_utc": _api_str(previous_snapshot),
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


def get_mobile_home_payload(connection: sqlite3.Connection) -> dict[str, object]:
    return {
        "server_time": datetime.now(tz=UTC).isoformat(),
        "boards": [
            _mobile_board_payload(get_board_payload(connection, board_name), title, market)
            for board_name, title, market in MOBILE_HOME_BOARDS
        ],
    }


def _mobile_board_payload(
    payload: dict[str, object],
    title: str,
    market: str,
) -> dict[str, object]:
    data_time = payload.get("snapshot_ts_utc")
    items = []
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "market": item.get("market"),
                "symbol": item.get("symbol"),
                "name": item.get("display_name"),
                "last_price": item.get("last_price"),
                "change_pct": item.get("change_pct"),
                "turnover": item.get("turnover_raw"),
                "volume": item.get("volume_raw"),
                "rank": item.get("rank"),
                "rank_change": item.get("rank_change"),
                "data_time": data_time,
            }
        )
    return {
        "key": payload.get("board_name"),
        "title": title,
        "market": market,
        "data_time": data_time,
        "items": items,
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
    path = Path(db_path)
    reserved = False
    try:
        with connect(path) as connection:
            reserved = _reserve_durable_board_refresh(
                connection,
                board_name=board_name,
                now_utc=_now_utc(),
                ttl_seconds=min_interval_seconds,
            )
    except Exception:
        _finish_board_refresh(board_name)
        return False
    if not reserved:
        _finish_board_refresh(board_name)
        return False

    thread = threading.Thread(
        target=_run_board_prices_refresh,
        args=(path, board_name),
        daemon=True,
    )
    thread.start()
    return True


def _run_board_prices_refresh(db_path: Path, board_name: str) -> None:
    try:
        with connect(db_path) as connection:
            refresh_board_prices_on_open(connection, board_name)
            _finish_durable_board_refresh(
                connection,
                board_name=board_name,
                status="succeeded",
                error=None,
                now_utc=_now_utc(),
            )
    except Exception as exc:
        try:
            with connect(db_path) as connection:
                _finish_durable_board_refresh(
                    connection,
                    board_name=board_name,
                    status="failed",
                    error=str(exc),
                    now_utc=_now_utc(),
                )
        except Exception:
            pass
        return
    finally:
        _finish_board_refresh(board_name)


def _reserve_durable_board_refresh(
    connection: sqlite3.Connection,
    *,
    board_name: str,
    now_utc: str,
    ttl_seconds: int,
) -> bool:
    row = connection.execute(
        """
        SELECT last_requested_at_utc, status
        FROM board_refresh_state
        WHERE board_name = ?
        """,
        (board_name,),
    ).fetchone()
    if row is not None and row["last_requested_at_utc"]:
        previous = _parse_utc(str(row["last_requested_at_utc"]))
        current = _parse_utc(now_utc)
        if (current - previous).total_seconds() < ttl_seconds:
            return False
    connection.execute(
        """
        INSERT INTO board_refresh_state (
            board_name,
            last_requested_at_utc,
            last_started_at_utc,
            status,
            updated_at_utc
        )
        VALUES (?, ?, ?, 'running', ?)
        ON CONFLICT(board_name) DO UPDATE SET
            last_requested_at_utc = excluded.last_requested_at_utc,
            last_started_at_utc = excluded.last_started_at_utc,
            status = 'running',
            last_error = NULL,
            updated_at_utc = excluded.updated_at_utc
        """,
        (board_name, now_utc, now_utc, now_utc),
    )
    return True


def _finish_durable_board_refresh(
    connection: sqlite3.Connection,
    *,
    board_name: str,
    status: str,
    error: str | None,
    now_utc: str,
) -> None:
    connection.execute(
        """
        UPDATE board_refresh_state
        SET
            last_finished_at_utc = ?,
            status = ?,
            last_error = ?,
            updated_at_utc = ?
        WHERE board_name = ?
        """,
        (now_utc, status, error, now_utc, board_name),
    )


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
    allow_metadata_refresh: bool = True,
) -> dict[str, object] | None:
    instrument_repository = InstrumentRepository(connection)
    instrument = instrument_repository.get_by_market_symbol(market, symbol)
    if instrument is None or instrument.instrument_id is None:
        return None
    if market == "CRYPTO" and allow_metadata_refresh:
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


def get_instrument_detail_payload(
    connection: sqlite3.Connection,
    market: str,
    symbol: str,
    *,
    daily_limit: int = 120,
    intraday_intervals: list[str] | None = None,
    intraday_limit: int = 96,
    include_funding: bool = True,
    allow_backfill: bool = True,
) -> dict[str, object] | None:
    instrument = get_instrument_payload(
        connection,
        market,
        symbol,
        include_funding=include_funding,
        allow_metadata_refresh=allow_backfill,
    )
    if instrument is None:
        return None

    daily_bars = get_daily_bars_payload(
        connection,
        market,
        symbol,
        limit=daily_limit,
        allow_backfill=allow_backfill,
    )
    resolved_intraday_intervals = (
        intraday_intervals
        if intraday_intervals is not None
        else (["1m", "5m", "15m", "8h"] if market in {"CRYPTO", "CRYPTO_FUTURES"} else [])
    )
    intraday_bars = [
        payload
        for payload in (
            get_intraday_bars_payload(
                connection,
                market,
                symbol,
                interval,
                limit=intraday_limit,
                allow_backfill=allow_backfill,
            )
            for interval in resolved_intraday_intervals
        )
        if payload["items"]
    ]
    available_periods = [
        str(payload["interval"])
        for payload in intraday_bars
        if payload["items"]
    ]
    if daily_bars["items"]:
        available_periods.append("1d")
    return {
        "instrument": instrument,
        "daily_bars": daily_bars,
        "intraday_bars": intraday_bars,
        "available_periods": available_periods,
    }


def get_mobile_instrument_detail_payload(
    connection: sqlite3.Connection,
    *,
    market: str,
    symbol: str,
    period: str = "1d",
    daily_limit: int = 120,
    intraday_limit: int = 96,
    allow_backfill: bool = True,
) -> dict[str, object] | None:
    intraday_periods = ["1m", "5m", "15m", "8h"] if market in {"CRYPTO", "CRYPTO_FUTURES"} else []
    detail = get_instrument_detail_payload(
        connection,
        market,
        symbol,
        daily_limit=daily_limit,
        intraday_intervals=[] if period == "1d" else [period],
        intraday_limit=intraday_limit,
        allow_backfill=allow_backfill,
    )
    if detail is None:
        return None
    period_detail = get_instrument_detail_payload(
        connection,
        market,
        symbol,
        daily_limit=1,
        intraday_intervals=intraday_periods,
        intraday_limit=1,
        allow_backfill=False,
    )
    instrument = detail["instrument"]
    if not isinstance(instrument, dict):
        return None
    snapshot = instrument.get("latest_snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}

    if period == "1d":
        bars_payload = detail["daily_bars"]
        bar_items = bars_payload.get("items", []) if isinstance(bars_payload, dict) else []
        bars = [_mobile_daily_bar_payload(item) for item in bar_items if isinstance(item, dict)]
    else:
        intraday_payloads = detail["intraday_bars"]
        matching_payload = None
        if isinstance(intraday_payloads, list):
            matching_payload = next(
                (
                    payload
                    for payload in intraday_payloads
                    if isinstance(payload, dict) and payload.get("interval") == period
                ),
                None,
            )
        bar_items = matching_payload.get("items", []) if isinstance(matching_payload, dict) else []
        bars = [_mobile_intraday_bar_payload(item) for item in bar_items if isinstance(item, dict)]

    available_periods = (
        period_detail.get("available_periods", [])
        if isinstance(period_detail, dict)
        else detail["available_periods"]
    )
    if bars and period not in available_periods:
        available_periods = [*available_periods, period]

    return {
        "instrument": {
            "market": instrument.get("market"),
            "symbol": instrument.get("symbol"),
            "name": instrument.get("display_name"),
            "asset_class": instrument.get("instrument_type"),
        },
        "snapshot": {
            "last_price": snapshot.get("last_price"),
            "change_pct": snapshot.get("change_pct"),
            "volume": snapshot.get("volume_raw"),
            "turnover": snapshot.get("turnover_raw"),
            "data_time": snapshot.get("snapshot_ts_utc"),
            "source": snapshot.get("source"),
        },
        "periods": available_periods,
        "bars": bars,
        "alert_markers": _get_mobile_alert_markers(
            connection,
            market=market,
            symbol=symbol,
            period=period,
            limit=100,
        ),
    }


def _get_mobile_alert_markers(
    connection: sqlite3.Connection,
    *,
    market: str,
    symbol: str,
    period: str,
    limit: int,
) -> list[dict[str, object]]:
    _ensure_mobile_alert_event_metadata_columns(connection)
    rows = connection.execute(
        """
        SELECT
            mobile_alert_event.mobile_alert_event_id,
            mobile_alert_event.triggered_at_utc,
            mobile_alert_event.observed_value,
            mobile_alert_event.message,
            mobile_alert_event.alert_metadata,
            mobile_alert_rule.condition_type
        FROM mobile_alert_event
        JOIN mobile_alert_rule
            ON mobile_alert_rule.mobile_alert_rule_id =
                mobile_alert_event.mobile_alert_rule_id
        WHERE mobile_alert_rule.market = ?
            AND mobile_alert_rule.symbol = ?
            AND mobile_alert_rule.source_type IN ('technical', 'strategy')
            AND mobile_alert_event.alert_metadata IS NOT NULL
        ORDER BY mobile_alert_event.triggered_at_utc DESC,
            mobile_alert_event.mobile_alert_event_id DESC
        LIMIT ?
        """,
        (market, symbol, limit),
    ).fetchall()
    markers = []
    seen_marker_keys: set[tuple[str, str, str]] = set()
    for row in rows:
        metadata = _parse_alert_metadata(row["alert_metadata"])
        marker_period = metadata.get("chart_period") or metadata.get("period")
        if marker_period != period:
            continue
        if str(row["condition_type"]) not in {"ma11_breakout_1d", "paper_strategy_pin"}:
            continue
        bar_time = _optional_str(metadata.get("bar_time"))
        price = _optional_float(metadata.get("price"))
        if bar_time is None or price is None:
            continue
        marker_key = (period, bar_time, str(row["condition_type"]))
        if marker_key in seen_marker_keys:
            continue
        seen_marker_keys.add(marker_key)
        markers.append(
            {
                "mobile_alert_event_id": int(row["mobile_alert_event_id"]),
                "time": bar_time,
                "price": price,
                "direction": _optional_str(metadata.get("direction")) or "up",
                "label": _optional_str(metadata.get("label"))
                or _mobile_alert_title_suffix(str(row["condition_type"])),
                "condition_type": str(row["condition_type"]),
                "condition_label": _optional_str(metadata.get("condition_label"))
                or str(row["condition_type"]),
                "ma11": _optional_float(metadata.get("ma11")),
                "volume_ratio": _optional_float(metadata.get("volume_ratio")),
                "triggered_at_utc": str(row["triggered_at_utc"]),
                "body": str(row["message"]),
            }
        )
    return list(reversed(markers))


def _mobile_daily_bar_payload(item: dict[str, object]) -> dict[str, object]:
    return {
        "time": item.get("trade_date"),
        "open": item.get("open"),
        "high": item.get("high"),
        "low": item.get("low"),
        "close": item.get("close"),
        "volume": item.get("volume_raw"),
        "turnover": item.get("turnover_raw"),
    }


def _mobile_intraday_bar_payload(item: dict[str, object]) -> dict[str, object]:
    return {
        "time": item.get("bar_start_ts_utc") or item.get("ts_utc"),
        "open": item.get("open"),
        "high": item.get("high"),
        "low": item.get("low"),
        "close": item.get("close"),
        "volume": item.get("volume_raw"),
        "turnover": item.get("turnover_raw"),
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
    allow_backfill: bool = True,
) -> dict[str, object]:
    instrument_repository = InstrumentRepository(connection)
    instrument = instrument_repository.get_by_market_symbol(market, symbol)
    resolved_limit = _clamp_limit(limit) if limit is not None else None
    if allow_backfill and market == "CRYPTO_FUTURES" and (
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
    should_backfill = allow_backfill and market == "CRYPTO_FUTURES" and (
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
    allow_backfill: bool = True,
) -> dict[str, object]:
    instrument_repository = InstrumentRepository(connection)
    instrument = instrument_repository.get_by_market_symbol(market, symbol)
    backfilled_missing_instrument = False
    if instrument is None or instrument.instrument_id is None:
        if allow_backfill and market in {"CRYPTO", "CRYPTO_FUTURES"}:
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
    if allow_backfill and market in {"CRYPTO", "CRYPTO_FUTURES"} and should_backfill:
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
        trade_date=_api_str(row["trade_date"]),
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
        bar_start_ts_utc=_api_str(row["bar_start_ts_utc"]),
        bar_end_ts_utc=_api_str(row["bar_end_ts_utc"]),
        trade_date_local=_api_str(row["trade_date_local"]),
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
        WHERE watchlist.is_active = TRUE
            AND instrument.is_active = TRUE
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
                "snapshot_ts_utc": _api_str(row["snapshot_ts_utc"]),
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
                "updated_at": _api_str(row["updated_at"]),
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
        VALUES (?, ?, ?, ?, TRUE, ?, ?)
        ON CONFLICT(push_token) DO UPDATE SET
            getui_cid = COALESCE(excluded.getui_cid, push_device.getui_cid),
            platform = excluded.platform,
            device_label = excluded.device_label,
            enabled = TRUE,
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
        (int(row["push_device_id"]),),
    ).fetchone()
    return {
        "push_device_id": int(row["push_device_id"]),
        "platform": str(row["platform"]),
        "push_token": str(row["push_token"]),
        "getui_cid": _optional_str(row["getui_cid"]),
        "device_label": _optional_str(row["device_label"]),
        "enabled": bool(row["enabled"]),
        "checkpoint": (
            _device_checkpoint_payload(checkpoint) if checkpoint is not None else None
        ),
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
            "SELECT indicator_id FROM indicator_definition WHERE indicator_id = ? AND enabled = TRUE",
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
    row = connection.execute(
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?)
        RETURNING *
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
            mobile_alert_event.alert_metadata,
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


def get_mobile_strategy_payload(connection: sqlite3.Connection) -> dict[str, object]:
    _ensure_mobile_strategy_tables(connection)
    _ensure_default_mobile_strategy_definitions(connection)
    strategy_rows = connection.execute(
        """
        SELECT strategy_id, name, description, execution_mode, enabled, updated_at_utc
        FROM strategy_definition
        ORDER BY strategy_id
        """
    ).fetchall()
    strategies: list[dict[str, object]] = []
    for strategy in strategy_rows:
        symbol_rows = connection.execute(
            """
            SELECT DISTINCT market, symbol
            FROM paper_position
            WHERE strategy_id = ?
            ORDER BY symbol
            """,
            (strategy["strategy_id"],),
        ).fetchall()
        if str(strategy["strategy_id"]) == PIN_PAPER_STRATEGY_ID:
            seen_symbols = {
                (str(symbol_row["market"]), str(symbol_row["symbol"]))
                for symbol_row in symbol_rows
            }
            symbol_rows = list(symbol_rows)
            for symbol in PIN_PAPER_STRATEGY_SYMBOLS:
                key = (PIN_PAPER_STRATEGY_MARKET, symbol)
                if key not in seen_symbols:
                    symbol_rows.append(
                        {
                            "market": PIN_PAPER_STRATEGY_MARKET,
                            "symbol": symbol,
                        }
                    )
        symbols = [
            _mobile_strategy_symbol_payload(
                connection,
                strategy_id=str(strategy["strategy_id"]),
                market=str(symbol_row["market"]),
                symbol=str(symbol_row["symbol"]),
            )
            for symbol_row in symbol_rows
        ]
        strategies.append(
            {
                "strategy_id": str(strategy["strategy_id"]),
                "name": str(strategy["name"]),
                "description": str(strategy["description"]),
                "execution_mode": str(strategy["execution_mode"]),
                "enabled": bool(strategy["enabled"]),
                "updated_at_utc": str(strategy["updated_at_utc"]),
                "symbols": symbols,
            }
        )
    return {"strategies": strategies}


def _ensure_default_mobile_strategy_definitions(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO strategy_definition (
            strategy_id, name, description, execution_mode, enabled, created_at_utc, updated_at_utc
        )
        VALUES (?, ?, ?, 'paper', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(strategy_id) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            execution_mode = excluded.execution_mode,
            enabled = excluded.enabled
        """,
        (
            PIN_PAPER_STRATEGY_ID,
            "Crypto Pin Rebound v1",
            "后台常驻：BTCUSDT/ETHUSDT futures trade + depth 实时插针策略，内部模拟开平仓并推送提醒。",
        ),
    )


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
            mobile_alert_event.alert_metadata,
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
            last_seen_mobile_alert_event_id = CASE
                WHEN device_checkpoint.last_seen_mobile_alert_event_id >
                    excluded.last_seen_mobile_alert_event_id
                THEN device_checkpoint.last_seen_mobile_alert_event_id
                ELSE excluded.last_seen_mobile_alert_event_id
            END,
            last_ack_mobile_alert_event_id = CASE
                WHEN device_checkpoint.last_ack_mobile_alert_event_id >
                    excluded.last_ack_mobile_alert_event_id
                THEN device_checkpoint.last_ack_mobile_alert_event_id
                ELSE excluded.last_ack_mobile_alert_event_id
            END,
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
        (enabled, now, rule_id),
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


def serve_api(
    db_path: Path | str | None,
    host: str,
    port: int,
    *,
    database_url: str | None = None,
    read_only_canary: bool = False,
) -> None:
    resolved_database_url = database_url or f"sqlite:///{Path(db_path)}"
    handler = _make_handler(
        resolved_database_url,
        sqlite_db_path=Path(db_path) if db_path is not None else None,
        read_only_canary=read_only_canary,
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"api listening: http://{host}:{port}")
    server.serve_forever()


def _make_handler(
    database_url: str | Path,
    *,
    sqlite_db_path: Path | None = None,
    read_only_canary: bool = False,
) -> type[BaseHTTPRequestHandler]:
    if isinstance(database_url, Path):
        sqlite_db_path = database_url
        database_url = f"sqlite:///{database_url}"
    read_only_canary = read_only_canary

    open_connection = create_database_connector(str(database_url))

    class MarketApiHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            if read_only_canary:
                self._write_json(
                    {"error": "read-only canary is read-only"},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            parsed = urlparse(self.path)
            if parsed.path == "/api/mobile/devices":
                try:
                    request_payload = self._read_json_object()
                    with open_connection() as connection:
                        response_payload = register_mobile_device(connection, request_payload)
                except ValueError as error:
                    self._write_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                    return
                self._write_json(response_payload)
                return

            if parsed.path == "/api/mobile/alert-rules":
                try:
                    request_payload = self._read_json_object()
                    with open_connection() as connection:
                        response_payload = create_mobile_alert_rule(connection, request_payload)
                except ValueError as error:
                    self._write_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                    return
                self._write_json(response_payload)
                return

            if parsed.path == "/api/mobile/alert-events/ack":
                try:
                    request_payload = self._read_json_object()
                    with open_connection() as connection:
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
            if read_only_canary:
                self._write_json(
                    {"error": "read-only canary is read-only"},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/mobile/alert-rules/"):
                try:
                    rule_id = int(parsed.path.removeprefix("/api/mobile/alert-rules/"))
                    request_payload = self._read_json_object()
                    with open_connection() as connection:
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
                with open_connection() as connection:
                    payload = get_health_payload(connection)
                self._write_json(payload)
                return

            if parsed.path == "/api/watchlists":
                with open_connection() as connection:
                    payload = get_watchlists_payload(connection)
                self._write_json(payload)
                return

            if parsed.path == "/api/jobs":
                with open_connection() as connection:
                    payload = get_jobs_payload(connection)
                self._write_json(payload)
                return

            if parsed.path == "/api/alerts/rules":
                with open_connection() as connection:
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
                with open_connection() as connection:
                    payload = get_alert_events_payload(connection, limit=limit)
                self._write_json(payload)
                return

            if parsed.path == "/api/mobile/alert-rules":
                query = parse_qs(parsed.query)
                push_token = _first_query(query, "push_token")
                if push_token is None:
                    self._write_json({"error": "push_token required"}, HTTPStatus.BAD_REQUEST)
                    return
                with open_connection() as connection:
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
                with open_connection() as connection:
                    payload = get_mobile_alert_events_payload(
                        connection,
                        push_token=push_token,
                        after_id=after_id,
                        limit=limit,
                    )
                self._write_json(payload)
                return

            if parsed.path == "/api/mobile/strategies":
                with open_connection() as connection:
                    payload = get_mobile_strategy_payload(connection)
                self._write_json(payload)
                return

            if parsed.path == "/api/mobile/debug/push-device":
                query = parse_qs(parsed.query)
                push_token = _first_query(query, "push_token")
                if push_token is None:
                    self._write_json({"error": "push_token required"}, HTTPStatus.BAD_REQUEST)
                    return
                with open_connection() as connection:
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
                with open_connection() as connection:
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
                if read_only_canary:
                    self._write_json(
                        {"error": "postgres canary does not support alert streaming"},
                        HTTPStatus.SERVICE_UNAVAILABLE,
                    )
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
                if not read_only_canary and sqlite_db_path is not None:
                    schedule_board_prices_refresh_on_open(sqlite_db_path, board_name)
                with open_connection() as connection:
                    payload = get_board_payload(connection, board_name)
                self._write_json(payload)
                return

            if parsed.path == "/api/mobile/home":
                with open_connection() as connection:
                    payload = get_mobile_home_payload(connection)
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
                with open_connection() as connection:
                    payload = get_instrument_payload(
                        connection,
                        market,
                        symbol,
                        include_funding=include_funding,
                        allow_metadata_refresh=not read_only_canary,
                    )
                if payload is None:
                    self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._write_json(payload)
                return

            if parsed.path == "/api/instrument-detail":
                query = parse_qs(parsed.query)
                market = _first_query(query, "market")
                symbol = _first_query(query, "symbol")
                if market is None or symbol is None:
                    self._write_json({"error": "market and symbol required"}, HTTPStatus.BAD_REQUEST)
                    return
                daily_limit = _optional_query_int(query, "daily_limit") or 120
                intraday_limit = _optional_query_int(query, "intraday_limit") or 96
                intraday_intervals = query.get("intraday_interval")
                with open_connection() as connection:
                    payload = get_instrument_detail_payload(
                        connection,
                        market,
                        symbol,
                        daily_limit=daily_limit,
                        intraday_intervals=intraday_intervals,
                        intraday_limit=intraday_limit,
                        include_funding=_first_query(query, "include_funding") != "0",
                        allow_backfill=not read_only_canary,
                    )
                if payload is None:
                    self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._write_json(payload)
                return

            if parsed.path == "/api/mobile/instrument-detail":
                query = parse_qs(parsed.query)
                market = _first_query(query, "market")
                symbol = _first_query(query, "symbol")
                if market is None or symbol is None:
                    self._write_json({"error": "market and symbol required"}, HTTPStatus.BAD_REQUEST)
                    return
                period = _first_query(query, "period") or "1d"
                daily_limit = _optional_query_int(query, "daily_limit") or 120
                intraday_limit = _optional_query_int(query, "intraday_limit") or 96
                with open_connection() as connection:
                    payload = get_mobile_instrument_detail_payload(
                        connection,
                        market=market,
                        symbol=symbol,
                        period=period,
                        daily_limit=daily_limit,
                        intraday_limit=intraday_limit,
                        allow_backfill=not read_only_canary,
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
                with open_connection() as connection:
                    payload = get_daily_bars_payload(
                        connection,
                        market,
                        symbol,
                        before_trade_date=before_trade_date,
                        limit=limit,
                        allow_backfill=not read_only_canary,
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
                with open_connection() as connection:
                    payload = get_intraday_bars_payload(
                        connection,
                        market,
                        symbol,
                        interval,
                        before_ts_utc=before_ts_utc,
                        limit=limit,
                        allow_backfill=not read_only_canary,
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
            body, encoding = self._encode_response_body(body)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if encoding is not None:
                self.send_header("Content-Encoding", encoding)
                self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            self.wfile.write(body)

        def _stream_mobile_alert_events(self, *, push_token: str, after_id: int) -> None:
            session_id = uuid.uuid4().hex
            started = time.monotonic()
            last_heartbeat = 0.0
            current_after_id = after_id
            try:
                with open_connection() as connection:
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
                    with open_connection() as connection:
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
                with open_connection() as connection:
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
            body, encoding = self._encode_response_body(body)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if encoding is not None:
                self.send_header("Content-Encoding", encoding)
                self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            self.wfile.write(body)

        def _encode_response_body(self, body: bytes) -> tuple[bytes, str | None]:
            if len(body) < 1024 or "gzip" not in self.headers.get("Accept-Encoding", ""):
                return body, None
            return gzip.compress(body), "gzip"

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
    return _api_str(row["snapshot_ts_utc"])


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
    return _api_str(row["snapshot_ts_utc"])


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
            AND watchlist.is_active = TRUE
        WHERE instrument.market = ?
            AND instrument.instrument_type = ?
            AND instrument.is_active = TRUE
            AND market_snapshot.turnover_raw IS NOT NULL
        """,
        (watchlist_name, market, instrument_type),
    ).fetchone()
    if row is None or row["trade_date_local"] is None:
        return None
    return _api_str(row["trade_date_local"])


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
        "snapshot_ts_utc": _api_str(row["snapshot_ts_utc"]),
        "trade_date_local": _api_str(row["trade_date_local"]),
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


def _api_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _format_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _to_ms(value: datetime) -> int:
    return int(value.astimezone(UTC).timestamp() * 1000)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _parse_alert_metadata(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ensure_mobile_alert_event_metadata_columns(connection: sqlite3.Connection) -> None:
    if getattr(connection, "backend", "sqlite") == "postgres":
        column = connection.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'mobile_alert_event'
                AND column_name = 'alert_metadata'
            LIMIT 1
            """
        ).fetchone()
        if column is None:
            connection.execute(
                "ALTER TABLE mobile_alert_event ADD COLUMN alert_metadata JSONB NOT NULL DEFAULT '{}'::jsonb"
            )
        return
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(mobile_alert_event)").fetchall()
    }
    if "alert_metadata" not in columns:
        connection.execute(
            "ALTER TABLE mobile_alert_event ADD COLUMN alert_metadata TEXT NOT NULL DEFAULT '{}'"
        )


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
    return _api_str(value)


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
    metadata = _parse_alert_metadata(row["alert_metadata"]) if "alert_metadata" in row.keys() else {}
    data: dict[str, object] = {
        "mobile_alert_event_id": int(row["mobile_alert_event_id"]),
        "market": str(row["market"]),
        "symbol": str(row["symbol"]),
        "url": f"/instrument.html?market={row['market']}&symbol={row['symbol']}",
    }
    period = _optional_str(metadata.get("period"))
    bar_time = _optional_str(metadata.get("bar_time"))
    if period is not None:
        data["period"] = period
    if bar_time is not None:
        data["bar_time"] = bar_time
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
        "data": data,
    }


def _mobile_strategy_symbol_payload(
    connection: sqlite3.Connection,
    *,
    strategy_id: str,
    market: str,
    symbol: str,
) -> dict[str, object]:
    position = connection.execute(
        """
        SELECT
            paper_position_id,
            side,
            status,
            entry_price,
            exit_price,
            opened_at_utc,
            closed_at_utc,
            realized_return_pct
        FROM paper_position
        WHERE strategy_id = ?
            AND market = ?
            AND symbol = ?
        ORDER BY
            CASE status WHEN 'open' THEN 0 ELSE 1 END,
            paper_position_id DESC
        LIMIT 1
        """,
        (strategy_id, market, symbol),
    ).fetchone()
    trade = connection.execute(
        """
        SELECT action, price, event_time_utc, realized_return_pct
        FROM paper_trade
        JOIN instrument
            ON instrument.instrument_id = paper_trade.instrument_id
        WHERE paper_trade.strategy_id = ?
            AND instrument.market = ?
            AND instrument.symbol = ?
        ORDER BY paper_trade.event_time_utc DESC, paper_trade.paper_trade_id DESC
        LIMIT 1
        """,
        (strategy_id, market, symbol),
    ).fetchone()
    snapshot = connection.execute(
        """
        SELECT latest_market_snapshot.last_price, latest_market_snapshot.snapshot_ts_utc
        FROM latest_market_snapshot
        JOIN instrument
            ON instrument.instrument_id = latest_market_snapshot.instrument_id
        WHERE instrument.market = ?
            AND instrument.symbol = ?
        LIMIT 1
        """,
        (market, symbol),
    ).fetchone()
    entry_price = _optional_float(position["entry_price"]) if position else None
    current_price = _optional_float(snapshot["last_price"]) if snapshot else None
    unrealized_return_pct = None
    if position and str(position["status"]) == "open" and entry_price not in (None, 0) and current_price is not None:
        unrealized_return_pct = ((current_price - entry_price) / entry_price) * 100
    return {
        "market": market,
        "symbol": symbol,
        "position_status": str(position["status"]) if position else "none",
        "side": str(position["side"]) if position else None,
        "entry_price": entry_price,
        "exit_price": _optional_float(position["exit_price"]) if position else None,
        "current_price": current_price,
        "opened_at_utc": str(position["opened_at_utc"]) if position else None,
        "closed_at_utc": str(position["closed_at_utc"]) if position and position["closed_at_utc"] is not None else None,
        "realized_return_pct": _optional_float(position["realized_return_pct"]) if position else None,
        "unrealized_return_pct": unrealized_return_pct,
        "last_trade": (
            {
                "action": str(trade["action"]),
                "price": _optional_float(trade["price"]),
                "event_time_utc": str(trade["event_time_utc"]),
                "realized_return_pct": _optional_float(trade["realized_return_pct"]),
            }
            if trade
            else None
        ),
    }


def _ensure_mobile_strategy_tables(connection: sqlite3.Connection) -> None:
    if getattr(connection, "backend", "sqlite") == "postgres":
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_definition (
                strategy_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                execution_mode TEXT NOT NULL DEFAULT 'paper',
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at_utc TIMESTAMPTZ NOT NULL,
                updated_at_utc TIMESTAMPTZ NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_position (
                paper_position_id BIGSERIAL PRIMARY KEY,
                strategy_id TEXT NOT NULL REFERENCES strategy_definition(strategy_id),
                instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id),
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_price DOUBLE PRECISION NOT NULL,
                exit_price DOUBLE PRECISION,
                opened_at_utc TIMESTAMPTZ NOT NULL,
                closed_at_utc TIMESTAMPTZ,
                realized_return_pct DOUBLE PRECISION,
                signal_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at_utc TIMESTAMPTZ NOT NULL,
                updated_at_utc TIMESTAMPTZ NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_trade (
                paper_trade_id BIGSERIAL PRIMARY KEY,
                strategy_id TEXT NOT NULL REFERENCES strategy_definition(strategy_id),
                paper_position_id BIGINT NOT NULL REFERENCES paper_position(paper_position_id),
                instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id),
                action TEXT NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                event_time_utc TIMESTAMPTZ NOT NULL,
                realized_return_pct DOUBLE PRECISION,
                signal_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at_utc TIMESTAMPTZ NOT NULL
            )
            """
        )
        return
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_definition (
            strategy_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            execution_mode TEXT NOT NULL DEFAULT 'paper',
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_position (
            paper_position_id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id TEXT NOT NULL,
            instrument_id INTEGER NOT NULL,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            opened_at_utc TEXT NOT NULL,
            closed_at_utc TEXT,
            realized_return_pct REAL,
            signal_payload TEXT NOT NULL DEFAULT '{}',
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_trade (
            paper_trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id TEXT NOT NULL,
            paper_position_id INTEGER NOT NULL,
            instrument_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            price REAL NOT NULL,
            event_time_utc TEXT NOT NULL,
            realized_return_pct REAL,
            signal_payload TEXT NOT NULL DEFAULT '{}',
            created_at_utc TEXT NOT NULL
        )
        """
    )


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
    if condition_type == "ma11_breakout_volume_15m":
        return "15m MA11 突破"
    if condition_type == "ma11_breakdown_15m":
        return "15m MA11 跌破"
    if condition_type == "ma11_breakout_1d":
        return "1d MA11 突破"
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
        if getattr(connection, "backend", "sqlite") == "postgres":
            connection.execute("SELECT 1").fetchone()
        else:
            connection.execute("PRAGMA quick_check").fetchone()
    except Exception:
        return False
    return True


def _journal_mode(connection: sqlite3.Connection) -> str:
    if getattr(connection, "backend", "sqlite") == "postgres":
        return "postgres"
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
