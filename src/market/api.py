from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from market.alerts import ALERT_METRICS, CHART_INDICATORS
from market.binance import RangeKlineFetcher, fetch_binance_klines_range
from market.crypto_gaps import fill_binance_1m_gaps
from market.db import connect
from market.models import IntradayBar
from market.repositories import (
    AlertEventRepository,
    AlertRuleRepository,
    DailyBarRepository,
    InstrumentRepository,
    IntradayBarRepository,
)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


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
            instrument.market,
            instrument.symbol,
            instrument.display_name,
            instrument.exchange,
            instrument.instrument_type
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
                "last_price": _optional_float(row["last_price"]),
                "volume_raw": _optional_float(row["volume_raw"]),
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


def get_instrument_payload(
    connection: sqlite3.Connection,
    market: str,
    symbol: str,
) -> dict[str, object] | None:
    instrument = InstrumentRepository(connection).get_by_market_symbol(market, symbol)
    if instrument is None or instrument.instrument_id is None:
        return None

    snapshot = _latest_snapshot(connection, instrument.instrument_id)
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
    }


def get_daily_bars_payload(
    connection: sqlite3.Connection,
    market: str,
    symbol: str,
) -> dict[str, object]:
    instrument = InstrumentRepository(connection).get_by_market_symbol(market, symbol)
    if instrument is None or instrument.instrument_id is None:
        return {"market": market, "symbol": symbol, "interval": "1d", "items": []}

    bars = DailyBarRepository(connection).list_for_instrument(instrument.instrument_id)
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
    gap_fetcher: RangeKlineFetcher = fetch_binance_klines_range,
    gap_min_request_interval_seconds: float = 1.0,
) -> dict[str, object]:
    instrument = InstrumentRepository(connection).get_by_market_symbol(market, symbol)
    if instrument is None or instrument.instrument_id is None:
        return {"market": market, "symbol": symbol, "interval": interval, "items": []}

    resolved_limit = _clamp_limit(limit)
    if before_ts_utc is not None and market == "CRYPTO":
        _ensure_crypto_intraday_window(
            connection,
            symbol=symbol,
            interval=interval,
            before_ts_utc=before_ts_utc,
            limit=resolved_limit,
            fetcher=gap_fetcher,
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
    symbol: str,
    interval: str,
    before_ts_utc: str,
    limit: int,
    fetcher: RangeKlineFetcher,
    min_request_interval_seconds: float,
) -> None:
    minutes = _interval_minutes(interval)
    if minutes is None:
        return
    end = _parse_utc(before_ts_utc)
    start = end - timedelta(minutes=minutes * limit)
    if minutes * limit > 1000:
        return
    fill_binance_1m_gaps(
        connection,
        symbols=[symbol],
        start_ts_utc=_format_utc(start),
        end_ts_utc=_format_utc(end),
        fetcher=fetcher,
        min_request_interval_seconds=min_request_interval_seconds,
    )


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

            if parsed.path.startswith("/api/boards/"):
                board_name = unquote(parsed.path.removeprefix("/api/boards/"))
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
                with connect(db_path) as connection:
                    payload = get_instrument_payload(connection, market, symbol)
                if payload is None:
                    self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._write_json(payload)
                return

            if parsed.path == "/api/bars/daily":
                query = parse_qs(parsed.query)
                market = _first_query(query, "market")
                symbol = _first_query(query, "symbol")
                if market is None or symbol is None:
                    self._write_json({"error": "market and symbol required"}, HTTPStatus.BAD_REQUEST)
                    return
                with connect(db_path) as connection:
                    payload = get_daily_bars_payload(connection, market, symbol)
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


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


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
