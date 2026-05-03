from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from market.collectors.binance_ws import (
    BINANCE_WS_CONNECTION_LIFETIME_SECONDS,
    BINANCE_WS_CONTROL_MESSAGE_INTERVAL_SECONDS,
    BinanceKlineWebSocketCollector,
    ControlMessageThrottle,
    build_combined_kline_stream_urls,
)
from market.db import connect, init_database
from market.models import IntradayBar
from market.repositories import InstrumentRepository, IntradayBarRepository
from market.binance import binance_symbol_to_instrument


class BinanceWebSocketTests(unittest.TestCase):
    def test_build_combined_kline_stream_urls_groups_lowercase_one_minute_streams(self):
        urls = build_combined_kline_stream_urls(
            ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            interval="1m",
            base_url="wss://example.test",
            max_streams_per_connection=2,
        )

        self.assertEqual(
            urls,
            [
                "wss://example.test/stream?streams=btcusdt@kline_1m/ethusdt@kline_1m",
                "wss://example.test/stream?streams=solusdt@kline_1m",
            ],
        )

    def test_websocket_limits_are_conservative_for_binance_control_messages(self):
        self.assertEqual(BINANCE_WS_CONTROL_MESSAGE_INTERVAL_SECONDS, 0.25)
        self.assertEqual(BINANCE_WS_CONNECTION_LIFETIME_SECONDS, 23 * 60 * 60)

    def test_control_message_throttle_keeps_subscribe_messages_below_limit(self):
        now = [100.0]
        sleeps = []

        def clock() -> float:
            return now[0]

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            now[0] += seconds

        throttle = ControlMessageThrottle(
            min_interval_seconds=0.25,
            clock=clock,
            sleep=sleep,
        )

        throttle.wait()
        now[0] += 0.10
        throttle.wait()

        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.15)

    def test_kline_collector_applies_one_minute_messages_without_aggregating(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            messages = [
                {
                    "stream": "btcusdt@kline_1m",
                    "data": {
                        "e": "kline",
                        "E": 1_776_000_030_000,
                        "s": "BTCUSDT",
                        "k": {
                            "t": 1_776_000_000_000,
                            "T": 1_776_000_059_999,
                            "s": "BTCUSDT",
                            "i": "1m",
                            "o": "64000.00",
                            "c": "64100.00",
                            "h": "64150.00",
                            "l": "63990.00",
                            "v": "2.5",
                            "q": "160250.00",
                            "x": False,
                        },
                    },
                }
            ]

            class FakeWebSocketApp:
                def __init__(
                    self,
                    url,
                    on_message,
                    on_error,
                    on_close,
                    on_open,
                    on_ping,
                    on_pong,
                ):
                    self.url = url
                    self.on_message = on_message
                    self.on_error = on_error
                    self.on_close = on_close
                    self.on_open = on_open
                    self.on_ping = on_ping
                    self.on_pong = on_pong

                def run_forever(self, **kwargs):
                    for message in messages:
                        self.on_message(self, json.dumps(message))

            created_urls = []

            def factory(url, on_message, on_error, on_close, on_open, on_ping, on_pong):
                created_urls.append(url)
                return FakeWebSocketApp(
                    url,
                    on_message,
                    on_error,
                    on_close,
                    on_open,
                    on_ping,
                    on_pong,
                )

            collector = BinanceKlineWebSocketCollector(
                db_path=db_path,
                symbols=["BTCUSDT"],
                interval="1m",
                websocket_app_factory=factory,
                logger=lambda _message: None,
            )

            result = collector.run_once()

            with connect(db_path) as connection:
                bars = connection.execute(
                    """
                    SELECT interval, close, turnover_raw, source
                    FROM bar_intraday
                    JOIN instrument
                        ON instrument.instrument_id = bar_intraday.instrument_id
                    WHERE instrument.symbol = 'BTCUSDT'
                    ORDER BY interval
                    """
                ).fetchall()
                ranking_count = connection.execute(
                    "SELECT count(*) FROM ranking_snapshot"
                ).fetchone()[0]

        self.assertEqual(
            created_urls,
            ["wss://stream.binance.com:9443/stream?streams=btcusdt@kline_1m"],
        )
        self.assertEqual(result.items_synced, 1)
        bars_by_interval = {row["interval"]: row for row in bars}
        self.assertEqual(bars_by_interval["1m"]["close"], 64100.0)
        self.assertEqual(bars_by_interval["1m"]["turnover_raw"], 160250.0)
        self.assertEqual(bars_by_interval["1m"]["source"], "binance_ws_kline")
        self.assertNotIn("5m", bars_by_interval)
        self.assertNotIn("15m", bars_by_interval)
        self.assertNotIn("8h", bars_by_interval)
        self.assertEqual(ranking_count, 0)

    def test_kline_collector_accepts_base_url_log_prefix_and_message_handler(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []
            logs = []
            message = {
                "stream": "btcusdt@kline_1m",
                "data": {
                    "e": "kline",
                    "E": 1_776_000_030_000,
                    "s": "BTCUSDT",
                    "k": {
                        "t": 1_776_000_000_000,
                        "T": 1_776_000_059_999,
                        "s": "BTCUSDT",
                        "i": "1m",
                        "o": "64000.00",
                        "c": "64100.00",
                        "h": "64150.00",
                        "l": "63990.00",
                        "v": "2.5",
                        "q": "160250.00",
                        "x": False,
                    },
                },
            }

            class FakeWebSocketApp:
                def __init__(self, url, on_message, on_error, on_close, on_open, on_ping, on_pong):
                    self.on_message = on_message

                def run_forever(self, **kwargs):
                    self.on_message(self, json.dumps(message))

            created_urls = []

            def factory(url, on_message, on_error, on_close, on_open, on_ping, on_pong):
                created_urls.append(url)
                return FakeWebSocketApp(
                    url,
                    on_message,
                    on_error,
                    on_close,
                    on_open,
                    on_ping,
                    on_pong,
                )

            def handler(connection, payload, *, aggregate=True):
                calls.append((connection, payload, aggregate))

            collector = BinanceKlineWebSocketCollector(
                db_path=db_path,
                symbols=["BTCUSDT"],
                interval="1m",
                ws_base_url="wss://fstream.binance.com",
                log_prefix="binance futures ws",
                message_handler=handler,
                websocket_app_factory=factory,
                logger=logs.append,
            )

            result = collector.run_once()

        self.assertEqual(
            created_urls,
            ["wss://fstream.binance.com/stream?streams=btcusdt@kline_1m"],
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], message)
        self.assertFalse(calls[0][2])
        self.assertEqual(result.items_synced, 1)
        self.assertTrue(any(message.startswith("binance futures ws connecting:") for message in logs))

    def test_kline_collector_logs_websocket_lifecycle_callbacks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            logs = []

            class FakeWebSocketApp:
                def __init__(self, url, on_message, on_error, on_close, on_open, on_ping, on_pong):
                    self.on_close = on_close
                    self.on_open = on_open
                    self.on_ping = on_ping
                    self.on_pong = on_pong

                def run_forever(self, **kwargs):
                    self.on_open(self)
                    self.on_ping(self, b"ping")
                    self.on_pong(self, b"pong")
                    self.on_close(self, 1000, "normal")

            def factory(url, on_message, on_error, on_close, on_open, on_ping, on_pong):
                return FakeWebSocketApp(
                    url,
                    on_message,
                    on_error,
                    on_close,
                    on_open,
                    on_ping,
                    on_pong,
                )

            collector = BinanceKlineWebSocketCollector(
                db_path=db_path,
                symbols=["BTCUSDT"],
                websocket_app_factory=factory,
                logger=logs.append,
            )

            collector.run_once()

        self.assertTrue(any("opened" in message for message in logs))
        self.assertTrue(any("ping" in message for message in logs))
        self.assertTrue(any("pong" in message for message in logs))
        self.assertTrue(any("closed" in message for message in logs))

    def test_kline_collector_skips_database_locked_message_without_closing_callback(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            logs = []
            message = {
                "stream": "btcusdt@kline_1m",
                "data": {
                    "e": "kline",
                    "E": 1_776_000_030_000,
                    "s": "BTCUSDT",
                    "k": {
                        "t": 1_776_000_000_000,
                        "T": 1_776_000_059_999,
                        "s": "BTCUSDT",
                        "i": "1m",
                        "o": "64000.00",
                        "c": "64100.00",
                        "h": "64150.00",
                        "l": "63990.00",
                        "v": "2.5",
                        "q": "160250.00",
                        "x": False,
                    },
                },
            }

            class FakeWebSocketApp:
                def __init__(self, url, on_message, on_error, on_close, on_open, on_ping, on_pong):
                    self.on_message = on_message
                    self.on_close = on_close

                def run_forever(self, **kwargs):
                    self.on_message(self, json.dumps(message))
                    self.on_close(self, 1000, "test close")

            def factory(url, on_message, on_error, on_close, on_open, on_ping, on_pong):
                return FakeWebSocketApp(
                    url,
                    on_message,
                    on_error,
                    on_close,
                    on_open,
                    on_ping,
                    on_pong,
                )

            collector = BinanceKlineWebSocketCollector(
                db_path=db_path,
                symbols=["BTCUSDT"],
                websocket_app_factory=factory,
                logger=logs.append,
            )

            with patch(
                "market.collectors.binance_ws.apply_binance_kline_event",
                side_effect=sqlite3.OperationalError("database is locked"),
            ):
                result = collector.run_once()

        self.assertEqual(result.items_synced, 0)
        self.assertTrue(any("database locked" in message for message in logs))

    def test_kline_collector_run_forever_reconnects_after_closed_cycle(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            messages = [
                {
                    "stream": "btcusdt@kline_1m",
                    "data": {
                        "e": "kline",
                        "E": 1_776_000_030_000,
                        "s": "BTCUSDT",
                        "k": {
                            "t": 1_776_000_000_000,
                            "T": 1_776_000_059_999,
                            "s": "BTCUSDT",
                            "i": "1m",
                            "o": "64000.00",
                            "c": "64100.00",
                            "h": "64150.00",
                            "l": "63990.00",
                            "v": "2.5",
                            "q": "160250.00",
                            "x": False,
                        },
                    },
                },
                {
                    "stream": "btcusdt@kline_1m",
                    "data": {
                        "e": "kline",
                        "E": 1_776_000_090_000,
                        "s": "BTCUSDT",
                        "k": {
                            "t": 1_776_000_060_000,
                            "T": 1_776_000_119_999,
                            "s": "BTCUSDT",
                            "i": "1m",
                            "o": "64100.00",
                            "c": "64120.00",
                            "h": "64125.00",
                            "l": "64090.00",
                            "v": "1.5",
                            "q": "96180.00",
                            "x": False,
                        },
                    },
                },
            ]
            runs = []
            sleeps = []

            class FakeWebSocketApp:
                def __init__(self, url, on_message, on_error, on_close, on_open, on_ping, on_pong):
                    self.on_message = on_message
                    self.on_close = on_close

                def run_forever(self, **kwargs):
                    message = messages[len(runs)]
                    runs.append(kwargs)
                    self.on_message(self, json.dumps(message))
                    self.on_close(self, 1000, "test close")

            def factory(url, on_message, on_error, on_close, on_open, on_ping, on_pong):
                return FakeWebSocketApp(
                    url,
                    on_message,
                    on_error,
                    on_close,
                    on_open,
                    on_ping,
                    on_pong,
                )

            collector = BinanceKlineWebSocketCollector(
                db_path=db_path,
                symbols=["BTCUSDT"],
                websocket_app_factory=factory,
                sleep=sleeps.append,
                logger=lambda _message: None,
            )

            result = collector.run_forever(
                reconnect_delay_seconds=3,
                max_reconnects=1,
            )

            with connect(db_path) as connection:
                count = connection.execute(
                    """
                    SELECT count(*)
                    FROM bar_intraday
                    JOIN instrument
                        ON instrument.instrument_id = bar_intraday.instrument_id
                    WHERE instrument.symbol = 'BTCUSDT'
                        AND bar_intraday.interval = '1m'
                    """
                ).fetchone()[0]

        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["ping_interval"], 0)
        self.assertIsNone(runs[0]["ping_timeout"])
        self.assertEqual(sleeps, [3])
        self.assertEqual(result.items_synced, 2)
        self.assertEqual(count, 2)

    def test_kline_collector_fills_disconnect_gap_before_reconnecting(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            gap_calls = []
            runs = []
            sleeps = []
            messages = [
                _kline_message("BTCUSDT", "2026-04-24T00:00:00Z", "64000.00"),
                _kline_message("BTCUSDT", "2026-04-24T00:03:00Z", "64030.00"),
            ]

            class FakeWebSocketApp:
                def __init__(self, url, on_message, on_error, on_close, on_open, on_ping, on_pong):
                    self.on_message = on_message
                    self.on_close = on_close

                def run_forever(self, **kwargs):
                    message = messages[len(runs)]
                    runs.append(kwargs)
                    self.on_message(self, json.dumps(message))
                    self.on_close(self, 1000, "test close")

            def factory(url, on_message, on_error, on_close, on_open, on_ping, on_pong):
                return FakeWebSocketApp(
                    url,
                    on_message,
                    on_error,
                    on_close,
                    on_open,
                    on_ping,
                    on_pong,
                )

            def fill_gaps(connection, symbols, start_ts_utc, end_ts_utc):
                gap_calls.append((symbols, start_ts_utc, end_ts_utc))
                instrument_id = InstrumentRepository(connection).upsert(
                    binance_symbol_to_instrument("BTCUSDT")
                )
                repository = IntradayBarRepository(connection)
                repository.upsert(_bar(instrument_id, "2026-04-24T00:01:00Z", 64010.0))
                repository.upsert(_bar(instrument_id, "2026-04-24T00:02:00Z", 64020.0))

            collector = BinanceKlineWebSocketCollector(
                db_path=db_path,
                symbols=["BTCUSDT"],
                websocket_app_factory=factory,
                sleep=sleeps.append,
                logger=lambda _message: None,
                gap_fill_on_reconnect=True,
                gap_filler=fill_gaps,
            )

            result = collector.run_forever(
                reconnect_delay_seconds=3,
                max_reconnects=1,
            )

            with connect(db_path) as connection:
                starts = [
                    row["bar_start_ts_utc"]
                    for row in connection.execute(
                        """
                        SELECT bar_start_ts_utc
                        FROM bar_intraday
                        JOIN instrument
                            ON instrument.instrument_id = bar_intraday.instrument_id
                        WHERE instrument.symbol = 'BTCUSDT'
                            AND bar_intraday.interval = '1m'
                        ORDER BY bar_start_ts_utc
                        """
                    ).fetchall()
                ]

        self.assertEqual(len(gap_calls), 1)
        self.assertEqual(gap_calls[0][0], ["BTCUSDT"])
        self.assertEqual(gap_calls[0][1], "2026-04-24T00:00:00Z")
        self.assertEqual(gap_calls[0][2], "2026-04-24T00:03:00Z")
        self.assertEqual(sleeps, [3])
        self.assertEqual(result.items_synced, 4)
        self.assertEqual(
            starts,
            [
                "2026-04-24T00:00:00Z",
                "2026-04-24T00:01:00Z",
                "2026-04-24T00:02:00Z",
                "2026-04-24T00:03:00Z",
            ],
        )


if __name__ == "__main__":
    unittest.main()


def _kline_message(symbol: str, start: str, close: str) -> dict[str, object]:
    start_ms = _ms(start)
    return {
        "stream": f"{symbol.lower()}@kline_1m",
        "data": {
            "e": "kline",
            "E": start_ms + 30_000,
            "s": symbol,
            "k": {
                "t": start_ms,
                "T": start_ms + 59_999,
                "s": symbol,
                "i": "1m",
                "o": close,
                "c": close,
                "h": close,
                "l": close,
                "v": "1",
                "q": close,
                "x": False,
            },
        },
    }


def _bar(instrument_id: int, start: str, close: float) -> IntradayBar:
    start_ms = _ms(start)
    end = start_ms + 60_000
    return IntradayBar(
        instrument_id=instrument_id,
        interval="1m",
        bar_start_ts_utc=start,
        bar_end_ts_utc=_format_ms(end),
        trade_date_local=start[:10],
        open=close,
        high=close,
        low=close,
        close=close,
        volume_raw=1.0,
        turnover_raw=close,
        is_closed_bar=True,
        source="binance_gap_fill",
    )


def _ms(value: str) -> int:
    from datetime import UTC, datetime

    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).timestamp() * 1000)


def _format_ms(value: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(value / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
