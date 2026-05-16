from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep as default_sleep
from typing import Callable

from market.collectors.base import CollectorResult
from market.crypto_gaps import fill_binance_1m_gaps
from market.db import connect_database_url
from market.push import deliver_mobile_alert_pushes
from market.realtime import (
    apply_binance_futures_kline_event,
    apply_binance_kline_event,
    parse_binance_kline_event,
)
from market.strategy.pin_realtime import RealtimePinPaperStrategyEngine

BINANCE_WS_BASE_URL = "wss://stream.binance.com:9443"
BINANCE_WS_CONTROL_MESSAGE_INTERVAL_SECONDS = 0.25
BINANCE_WS_CONNECTION_LIFETIME_SECONDS = 23 * 60 * 60


class ControlMessageThrottle:
    def __init__(
        self,
        min_interval_seconds: float,
        *,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], None] = default_sleep,
    ) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.clock = clock
        self.sleep = sleep
        self._last_message_at: float | None = None

    def wait(self) -> None:
        now = self.clock()
        if self._last_message_at is not None:
            elapsed = now - self._last_message_at
            remaining = self.min_interval_seconds - elapsed
            if remaining > 0:
                self.sleep(remaining)
                now = self.clock()
        self._last_message_at = now


class BinanceKlineWebSocketCollector:
    source_name = "binance_ws_kline"

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        database_url: str | None = None,
        symbols: list[str],
        interval: str = "1m",
        max_streams_per_connection: int = 200,
        websocket_app_factory=None,
        logger: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = default_sleep,
        ping_interval_seconds: int = 0,
        ping_timeout_seconds: int | None = None,
        gap_fill_on_reconnect: bool = False,
        gap_filler=None,
        ws_base_url: str = BINANCE_WS_BASE_URL,
        log_prefix: str = "binance ws",
        message_handler=None,
    ) -> None:
        if database_url is None:
            if db_path is None:
                raise ValueError("db_path or database_url is required")
            database_url = f"sqlite:///{Path(db_path)}"
        self.database_url = database_url
        self.symbols = [symbol.upper() for symbol in symbols]
        self.interval = interval
        self.max_streams_per_connection = max_streams_per_connection
        self.ws_base_url = ws_base_url
        self.log_prefix = log_prefix
        self.message_handler = message_handler
        self.websocket_app_factory = websocket_app_factory or _default_websocket_app_factory
        self.logger = logger or _default_logger
        self.sleep = sleep
        self.ping_interval_seconds = ping_interval_seconds
        self.ping_timeout_seconds = ping_timeout_seconds
        self.gap_fill_on_reconnect = gap_fill_on_reconnect
        self.gap_filler = gap_filler or _default_gap_filler
        self.items_synced = 0
        self.last_error: str | None = None
        self._latest_bar_ts_utc: str | None = None

    def run_once(self) -> CollectorResult:
        for url in build_combined_kline_stream_urls(
            self.symbols,
            interval=self.interval,
            base_url=self.ws_base_url,
            max_streams_per_connection=self.max_streams_per_connection,
        ):
            app = self.websocket_app_factory(
                url,
                self._on_message,
                self._on_error,
                self._on_close,
                self._on_open,
                self._on_ping,
                self._on_pong,
            )
            self._log(f"connecting: url={_redact_stream_url(url)}")
            app.run_forever(
                ping_interval=self.ping_interval_seconds,
                ping_timeout=self.ping_timeout_seconds,
            )
        return CollectorResult(
            source_name=self.source_name,
            items_synced=self.items_synced,
            metadata={
                "symbols": self.symbols,
                "interval": self.interval,
                "connection_lifetime_seconds": BINANCE_WS_CONNECTION_LIFETIME_SECONDS,
            },
        )

    def run_forever(
        self,
        *,
        reconnect_delay_seconds: float = 5.0,
        max_reconnects: int | None = None,
    ) -> CollectorResult:
        reconnects = 0
        while True:
            try:
                result = self.run_once()
            except KeyboardInterrupt:
                raise
            except Exception as error:
                self.last_error = str(error)
                self._log(f"cycle error: {type(error).__name__}: {error}")
                result = self._result()
            if max_reconnects is not None and reconnects >= max_reconnects:
                return result
            reconnects += 1
            self._log(
                f"reconnect scheduled: delay={reconnect_delay_seconds}s reconnect={reconnects}"
            )
            self.sleep(reconnect_delay_seconds)

    def _on_message(self, _app, message: str) -> None:
        try:
            payload = json.loads(message)
            bar_start_ts_utc = _extract_kline_start_ts(payload)
            with connect_database_url(self.database_url) as connection:
                if self.gap_fill_on_reconnect and bar_start_ts_utc is not None:
                    self._fill_gap_before_bar(connection, bar_start_ts_utc)
                handler = self.message_handler or apply_binance_kline_event
                handler(connection, payload, aggregate=False)
                self.items_synced += 1
            if bar_start_ts_utc is not None:
                self._latest_bar_ts_utc = bar_start_ts_utc
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower():
                self.last_error = str(error)
                self._log(f"message skipped: database locked: {error}")
                return
            raise
        except Exception as error:
            self.last_error = str(error)
            self._log(f"message error: {type(error).__name__}: {error}")
            raise

    def _on_error(self, _app, error) -> None:
        self.last_error = str(error)
        self._log(f"error: {error}")

    def _on_close(self, _app, status_code, message) -> None:
        self._log(
            f"closed: status={status_code} message={message!r} messages={self.items_synced}"
        )

    def _on_open(self, _app) -> None:
        self._log(
            f"opened: symbols={len(self.symbols)} interval={self.interval}"
        )

    def _on_ping(self, _app, message) -> None:
        self._log(f"ping: bytes={len(message or b'')}")

    def _on_pong(self, _app, message) -> None:
        self._log(f"pong: bytes={len(message or b'')}")

    def _result(self) -> CollectorResult:
        return CollectorResult(
            source_name=self.source_name,
            items_synced=self.items_synced,
            metadata={
                "symbols": self.symbols,
                "interval": self.interval,
                "connection_lifetime_seconds": BINANCE_WS_CONNECTION_LIFETIME_SECONDS,
                "last_error": self.last_error,
            },
        )

    def _log(self, message: str) -> None:
        self.logger(f"{self.log_prefix} {message}")

    def _fill_gap_before_bar(
        self,
        connection: sqlite3.Connection,
        current_bar_ts_utc: str,
    ) -> None:
        if self.interval != "1m":
            return
        if self._latest_bar_ts_utc is None:
            return
        if _utc_diff_seconds(self._latest_bar_ts_utc, current_bar_ts_utc) <= 60:
            return
        self._log(
            f"gap check: start={self._latest_bar_ts_utc} end={current_bar_ts_utc}"
        )
        try:
            before_count = _count_1m_bars(
                connection,
                self.symbols,
                self._latest_bar_ts_utc,
                current_bar_ts_utc,
            )
            self.gap_filler(
                connection,
                self.symbols,
                self._latest_bar_ts_utc,
                current_bar_ts_utc,
            )
            after_count = _count_1m_bars(
                connection,
                self.symbols,
                self._latest_bar_ts_utc,
                current_bar_ts_utc,
            )
            written = max(after_count - before_count, 0)
            self.items_synced += written
            self._log(f"gap check done: bars_written={written}")
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower():
                self.last_error = str(error)
                self._log(f"gap check skipped: database locked: {error}")
                return
            raise
        except Exception as error:
            self.last_error = str(error)
            self._log(f"gap check error: {type(error).__name__}: {error}")


class BinanceFuturesTradeBookWebSocketCollector:
    source_name = "binance_futures_trade_book_ws"

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        database_url: str | None = None,
        symbols: list[str],
        max_streams_per_connection: int = 100,
        websocket_app_factory=None,
        logger: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = default_sleep,
        ping_interval_seconds: int = 0,
        ping_timeout_seconds: int | None = None,
        ws_base_url: str = "wss://fstream.binance.com/market",
        engine: RealtimePinPaperStrategyEngine | None = None,
        archive_dir: Path | str | None = None,
        push_deliverer=deliver_mobile_alert_pushes,
        include_kline_stream: bool | None = None,
    ) -> None:
        if database_url is None:
            if db_path is None:
                raise ValueError("db_path or database_url is required")
            database_url = f"sqlite:///{Path(db_path)}"
        self.database_url = database_url
        self.symbols = [symbol.upper() for symbol in symbols]
        self.max_streams_per_connection = max_streams_per_connection
        self.websocket_app_factory = websocket_app_factory or _default_websocket_app_factory
        self.logger = logger or _default_logger
        self.sleep = sleep
        self.ping_interval_seconds = ping_interval_seconds
        self.ping_timeout_seconds = ping_timeout_seconds
        self.ws_base_url = ws_base_url
        self.engine = engine or RealtimePinPaperStrategyEngine(symbols=self.symbols)
        self.archive_dir = Path(archive_dir) if archive_dir is not None else None
        self.push_deliverer = push_deliverer
        self.include_kline_stream = (
            include_kline_stream
            if include_kline_stream is not None
            else getattr(self.engine, "kline_curve_candidate_config", None) is not None
        )
        self.items_synced = 0
        self.strategy_events = 0
        self.last_error: str | None = None

    def run_once(self) -> CollectorResult:
        for url in build_combined_futures_trade_book_stream_urls(
            self.symbols,
            base_url=self.ws_base_url,
            max_streams_per_connection=self.max_streams_per_connection,
            include_kline_1m=self.include_kline_stream,
        ):
            app = self.websocket_app_factory(
                url,
                self._on_message,
                self._on_error,
                self._on_close,
                self._on_open,
                self._on_ping,
                self._on_pong,
            )
            self._log(f"connecting: url={_redact_stream_url(url)}")
            app.run_forever(
                ping_interval=self.ping_interval_seconds,
                ping_timeout=self.ping_timeout_seconds,
            )
        return self._result()

    def run_forever(
        self,
        *,
        reconnect_delay_seconds: float = 5.0,
        max_reconnects: int | None = None,
    ) -> CollectorResult:
        reconnects = 0
        while True:
            try:
                result = self.run_once()
            except KeyboardInterrupt:
                raise
            except Exception as error:
                self.last_error = str(error)
                self._log(f"cycle error: {type(error).__name__}: {error}")
                result = self._result()
            if max_reconnects is not None and reconnects >= max_reconnects:
                return result
            reconnects += 1
            self._log(
                f"reconnect scheduled: delay={reconnect_delay_seconds}s reconnect={reconnects}"
            )
            self.sleep(reconnect_delay_seconds)

    def _on_message(self, _app, message: str) -> None:
        try:
            payload = json.loads(message)
            data = payload.get("data", payload)
            if not isinstance(data, dict):
                return
            event_type = str(data.get("e") or "")
            self._archive_message(payload)
            with connect_database_url(self.database_url) as connection:
                results = []
                if event_type == "depthUpdate":
                    self.engine.on_depth(connection, payload)
                elif event_type == "aggTrade":
                    raw_result = self.engine.on_trade(connection, payload)
                    results = raw_result if isinstance(raw_result, list) else [raw_result]
                elif event_type == "kline":
                    bar = apply_binance_futures_kline_event(connection, payload)
                    if bar.is_closed_bar and bar.interval == "1m":
                        kline = data.get("k")
                        symbol = str(
                            kline.get("s") if isinstance(kline, dict) else data.get("s")
                        ).upper()
                        self.engine.on_kline_bar(
                            symbol,
                            {
                                "timestamp": _epoch_seconds(bar.bar_start_ts_utc),
                                "open": float(bar.open or 0.0),
                                "high": float(bar.high or 0.0),
                                "low": float(bar.low or 0.0),
                                "close": float(bar.close or 0.0),
                                "volume": float(bar.volume_raw or 0.0),
                                "quote_volume": float(bar.turnover_raw or 0.0),
                            },
                        )
                else:
                    return
                self.items_synced += 1
                for result in results:
                    if result is None:
                        continue
                    self.strategy_events += 1
                    self.push_deliverer(connection, now_utc=result.event_time_utc)
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower():
                self.last_error = str(error)
                self._log(f"message skipped: database locked: {error}")
                return
            raise
        except Exception as error:
            self.last_error = str(error)
            self._log(f"message error: {type(error).__name__}: {error}")
            raise

    def _on_error(self, _app, error) -> None:
        self.last_error = str(error)
        self._log(f"error: {error}")

    def _on_close(self, _app, status_code, message) -> None:
        self._log(
            f"closed: status={status_code} message={message!r} messages={self.items_synced}"
        )

    def _on_open(self, _app) -> None:
        self._log(f"opened: symbols={len(self.symbols)}")

    def _on_ping(self, _app, message) -> None:
        self._log(f"ping: bytes={len(message or b'')}")

    def _on_pong(self, _app, message) -> None:
        self._log(f"pong: bytes={len(message or b'')}")

    def _result(self) -> CollectorResult:
        return CollectorResult(
            source_name=self.source_name,
            items_synced=self.items_synced,
            metadata={
                "symbols": self.symbols,
                "strategy_events": self.strategy_events,
                "last_error": self.last_error,
            },
        )

    def _log(self, message: str) -> None:
        self.logger(f"binance futures trade book ws {message}")

    def _archive_message(self, payload: dict[str, object]) -> None:
        if self.archive_dir is None:
            return
        day = _ws_payload_utc_day(payload)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.archive_dir / f"{day}.jsonl.gz"
        with gzip.open(archive_path, "at", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")


def build_combined_kline_stream_urls(
    symbols: list[str],
    *,
    interval: str,
    base_url: str = BINANCE_WS_BASE_URL,
    max_streams_per_connection: int = 200,
) -> list[str]:
    streams = [f"{symbol.lower()}@kline_{interval}" for symbol in symbols]
    grouped = [
        streams[index : index + max_streams_per_connection]
        for index in range(0, len(streams), max_streams_per_connection)
    ]
    return [f"{base_url}/stream?streams={'/'.join(group)}" for group in grouped]


def build_combined_futures_trade_book_stream_urls(
    symbols: list[str],
    *,
    base_url: str = "wss://fstream.binance.com/market",
    max_streams_per_connection: int = 100,
    include_kline_1m: bool = False,
) -> list[str]:
    streams: list[str] = []
    for symbol in symbols:
        normalized = symbol.lower()
        streams.append(f"{normalized}@aggTrade")
        streams.append(f"{normalized}@depth20@100ms")
        if include_kline_1m:
            streams.append(f"{normalized}@kline_1m")
    grouped = [
        streams[index : index + max_streams_per_connection]
        for index in range(0, len(streams), max_streams_per_connection)
    ]
    return [f"{base_url}/stream?streams={'/'.join(group)}" for group in grouped]


def _epoch_seconds(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _default_websocket_app_factory(
    url,
    on_message,
    on_error,
    on_close,
    on_open,
    on_ping,
    on_pong,
):
    try:
        import websocket
    except ImportError as error:
        raise RuntimeError(
            "websocket-client is required to run Binance WebSocket collectors"
        ) from error
    return websocket.WebSocketApp(
        url,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open,
        on_ping=on_ping,
        on_pong=on_pong,
    )


def _default_logger(message: str) -> None:
    print(message, flush=True)


def _default_gap_filler(
    connection: sqlite3.Connection,
    symbols: list[str],
    start_ts_utc: str,
    end_ts_utc: str,
) -> None:
    fill_binance_1m_gaps(
        connection,
        symbols=symbols,
        start_ts_utc=start_ts_utc,
        end_ts_utc=end_ts_utc,
    )


def _count_1m_bars(
    connection: sqlite3.Connection,
    symbols: list[str],
    start_ts_utc: str,
    end_ts_utc: str,
) -> int:
    if not symbols:
        return 0
    placeholders = ",".join("?" for _symbol in symbols)
    row = connection.execute(
        f"""
        SELECT count(*)
        FROM bar_intraday
        JOIN instrument
            ON instrument.instrument_id = bar_intraday.instrument_id
        WHERE instrument.symbol IN ({placeholders})
            AND bar_intraday.interval = '1m'
            AND bar_intraday.bar_start_ts_utc >= ?
            AND bar_intraday.bar_start_ts_utc <= ?
        """,
        [*symbols, start_ts_utc, end_ts_utc],
    ).fetchone()
    return int(row[0])


def _extract_kline_start_ts(payload: dict[str, object]) -> str | None:
    try:
        bar = parse_binance_kline_event(
            payload,
            instrument_id=0,
            timezone_name="UTC",
        )
    except Exception:
        return None
    return bar.bar_start_ts_utc


def _utc_diff_seconds(start_ts_utc: str, end_ts_utc: str) -> int:
    from datetime import datetime

    start = datetime.fromisoformat(start_ts_utc.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_ts_utc.replace("Z", "+00:00"))
    return int((end - start).total_seconds())


def _redact_stream_url(url: str) -> str:
    if "streams=" not in url:
        return url
    prefix, streams = url.split("streams=", 1)
    stream_count = len([stream for stream in streams.split("/") if stream])
    return f"{prefix}streams=<{stream_count} streams>"


def _ws_payload_utc_day(payload: dict[str, object]) -> str:
    data = payload.get("data", payload)
    timestamp_ms = None
    if isinstance(data, dict):
        for key in ("T", "E"):
            value = data.get(key)
            if value is not None:
                timestamp_ms = int(value)
                break
    if timestamp_ms is None:
        return datetime.now(tz=UTC).date().isoformat()
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=UTC).date().isoformat()
