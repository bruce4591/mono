from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from time import monotonic, sleep as default_sleep
from typing import Callable

from market.collectors.base import CollectorResult
from market.db import connect
from market.realtime import apply_binance_kline_event

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
        db_path: Path | str,
        symbols: list[str],
        interval: str = "1m",
        max_streams_per_connection: int = 200,
        websocket_app_factory=None,
    ) -> None:
        self.db_path = Path(db_path)
        self.symbols = [symbol.upper() for symbol in symbols]
        self.interval = interval
        self.max_streams_per_connection = max_streams_per_connection
        self.websocket_app_factory = websocket_app_factory or _default_websocket_app_factory
        self.items_synced = 0
        self.last_error: str | None = None

    def run_once(self) -> CollectorResult:
        for url in build_combined_kline_stream_urls(
            self.symbols,
            interval=self.interval,
            max_streams_per_connection=self.max_streams_per_connection,
        ):
            app = self.websocket_app_factory(
                url,
                self._on_message,
                self._on_error,
                self._on_close,
            )
            app.run_forever(ping_interval=15, ping_timeout=10)
        return CollectorResult(
            source_name=self.source_name,
            items_synced=self.items_synced,
            metadata={
                "symbols": self.symbols,
                "interval": self.interval,
                "connection_lifetime_seconds": BINANCE_WS_CONNECTION_LIFETIME_SECONDS,
            },
        )

    def _on_message(self, _app, message: str) -> None:
        payload = json.loads(message)
        with connect(self.db_path) as connection:
            apply_binance_kline_event(connection, payload)
            self.items_synced += 1

    def _on_error(self, _app, error) -> None:
        self.last_error = str(error)

    def _on_close(self, _app, _status_code, _message) -> None:
        return


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


def _default_websocket_app_factory(url, on_message, on_error, on_close):
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
    )
