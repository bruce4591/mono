from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from market.collectors.binance_ws import (
    BINANCE_WS_CONNECTION_LIFETIME_SECONDS,
    BINANCE_WS_CONTROL_MESSAGE_INTERVAL_SECONDS,
    BinanceKlineWebSocketCollector,
    ControlMessageThrottle,
    build_combined_kline_stream_urls,
)
from market.db import connect, init_database


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

    def test_kline_collector_applies_one_minute_messages_aggregates_without_refreshing_rankings(self):
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
                def __init__(self, url, on_message, on_error, on_close):
                    self.url = url
                    self.on_message = on_message
                    self.on_error = on_error
                    self.on_close = on_close

                def run_forever(self, **kwargs):
                    for message in messages:
                        self.on_message(self, json.dumps(message))

            created_urls = []

            def factory(url, on_message, on_error, on_close):
                created_urls.append(url)
                return FakeWebSocketApp(url, on_message, on_error, on_close)

            collector = BinanceKlineWebSocketCollector(
                db_path=db_path,
                symbols=["BTCUSDT"],
                interval="1m",
                websocket_app_factory=factory,
            )

            result = collector.run_once()

            with connect(db_path) as connection:
                bar = connection.execute(
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
        bars_by_interval = {row["interval"]: row for row in bar}
        self.assertEqual(bars_by_interval["1m"]["close"], 64100.0)
        self.assertEqual(bars_by_interval["1m"]["turnover_raw"], 160250.0)
        self.assertEqual(bars_by_interval["1m"]["source"], "binance_ws_kline")
        self.assertEqual(bars_by_interval["5m"]["close"], 64100.0)
        self.assertEqual(bars_by_interval["15m"]["source"], "aggregate_1m")
        self.assertEqual(bars_by_interval["8h"]["source"], "aggregate_1m")
        self.assertEqual(ranking_count, 0)


if __name__ == "__main__":
    unittest.main()
