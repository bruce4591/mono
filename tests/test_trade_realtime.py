from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from market.db import connect, init_database
from market.alerts import evaluate_mobile_alert_rules
from market.api import get_mobile_strategy_payload
from market.realtime import parse_binance_futures_depth_event, parse_binance_futures_trade_event
from market.strategy.pin_realtime import RealtimePinPaperStrategyEngine
from market.strategy.paper import PaperStrategyResult, evaluate_pin_paper_strategy
from market.strategy.paper import evaluate_pin_paper_strategy_events
from market.trade.pin_label_backtest import CandidateSignalScorer, KlineCurveCandidateConfig
from market.trade.pin_stage_replay import EntryOrderPlan, PinStageConfig, Stage, StageEvent
from market.collectors.binance_ws import (
    BinanceFuturesTradeBookWebSocketCollector,
    build_combined_futures_trade_book_stream_urls,
)


class RealtimeTradeStrategyTests(unittest.TestCase):
    def test_build_combined_futures_trade_book_stream_urls(self):
        urls = build_combined_futures_trade_book_stream_urls(
            ["BTCUSDT", "ETHUSDT"],
            base_url="wss://fstream.binance.com/market",
        )

        self.assertEqual(
            urls,
            [
                "wss://fstream.binance.com/market/stream?streams="
                "btcusdt@aggTrade/btcusdt@depth20@100ms/"
                "ethusdt@aggTrade/ethusdt@depth20@100ms"
            ],
        )

    def test_build_combined_futures_trade_book_stream_urls_can_include_kline_streams(self):
        urls = build_combined_futures_trade_book_stream_urls(
            ["BTCUSDT"],
            base_url="wss://fstream.binance.com/market",
            include_kline_1m=True,
        )

        self.assertEqual(
            urls,
            [
                "wss://fstream.binance.com/market/stream?streams="
                "btcusdt@aggTrade/btcusdt@depth20@100ms/btcusdt@kline_1m"
            ],
        )

    def test_parse_binance_futures_agg_trade_event(self):
        event = parse_binance_futures_trade_event(
            {
                "stream": "btcusdt@aggTrade",
                "data": {
                    "e": "aggTrade",
                    "E": 1_776_000_000_111,
                    "T": 1_776_000_000_100,
                    "s": "BTCUSDT",
                    "p": "64000.50",
                    "q": "2.25",
                    "m": True,
                },
            }
        )

        self.assertEqual(event.symbol, "BTCUSDT")
        self.assertEqual(event.timestamp, 1_776_000_000.1)
        self.assertEqual(event.price, 64000.5)
        self.assertEqual(event.size, 2.25)
        self.assertEqual(event.side, "Sell")

    def test_parse_binance_futures_partial_depth_event(self):
        event = parse_binance_futures_depth_event(
            {
                "stream": "ethusdt@depth20@100ms",
                "data": {
                    "e": "depthUpdate",
                    "E": 1_776_000_000_222,
                    "T": 1_776_000_000_200,
                    "s": "ETHUSDT",
                    "b": [["3200.00", "11.5"], ["3199.50", "5.0"]],
                    "a": [["3200.50", "8.0"], ["3201.00", "6.0"]],
                },
            }
        )

        self.assertEqual(event.symbol, "ETHUSDT")
        self.assertEqual(event.timestamp, 1_776_000_000.2)
        self.assertEqual(event.bids[0], ("3200.00", "11.5"))
        self.assertEqual(event.asks[0], ("3200.50", "8.0"))

    def test_pin_strategy_opens_and_closes_paper_position_from_live_trade_book_events(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                open_result = evaluate_pin_paper_strategy(
                    connection,
                    market="CRYPTO_FUTURES",
                    symbol="BTCUSDT",
                    stage_event={
                        "timestamp": "2026-04-12T13:21:05Z",
                        "stage": "rebound_confirmed",
                        "direction": "down_flush",
                        "price": 64000.0,
                        "strength": 0.72,
                        "trigger_window_seconds": 15.0,
                    },
                )
                close_result = evaluate_pin_paper_strategy(
                    connection,
                    market="CRYPTO_FUTURES",
                    symbol="BTCUSDT",
                    stage_event={
                        "timestamp": "2026-04-12T13:35:05Z",
                        "stage": "trend_break",
                        "direction": "up_squeeze",
                        "price": 64800.0,
                        "strength": 0.41,
                        "trigger_window_seconds": 15.0,
                    },
                )
                position_rows = connection.execute(
                    "SELECT status, entry_price, exit_price, realized_return_pct FROM paper_position"
                ).fetchall()
                trade_rows = connection.execute(
                    "SELECT action, price, realized_return_pct FROM paper_trade ORDER BY paper_trade_id"
                ).fetchall()

        self.assertEqual(open_result.action, "open_long")
        self.assertEqual(close_result.action, "close_long")
        self.assertEqual(position_rows[0]["status"], "closed")
        self.assertEqual(position_rows[0]["entry_price"], 64000.0)
        self.assertEqual(position_rows[0]["exit_price"], 64800.0)
        self.assertAlmostEqual(position_rows[0]["realized_return_pct"], 1.12925)
        self.assertEqual([row["action"] for row in trade_rows], ["open_long", "close_long"])
        self.assertAlmostEqual(trade_rows[1]["realized_return_pct"], 1.12925)

    def test_paper_strategy_opens_multiple_positions_from_adaptive_slices(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                results = evaluate_pin_paper_strategy_events(
                    connection,
                    market="CRYPTO_FUTURES",
                    symbol="BTCUSDT",
                    stage_event={
                        "timestamp": "2026-04-12T13:21:05Z",
                        "stage": "rebound_confirmed",
                        "direction": "down_flush",
                        "price": 64000.0,
                        "strength": 0.72,
                        "trigger_window_seconds": 15.0,
                        "entry_order_slices": [
                            {"trigger": "rebound_confirmed", "price": 64000.0, "notional": 3000.0},
                            {"trigger": "rebound_confirmed", "price": 63950.0, "notional": 5000.0},
                            {"trigger": "half_retest", "price": 63800.0, "notional": 2000.0},
                        ],
                    },
                )
                positions = connection.execute(
                    """
                    SELECT status, entry_price, entry_notional, quantity, entry_fee
                    FROM paper_position
                    WHERE status = 'open'
                    ORDER BY paper_position_id
                    """
                ).fetchall()
                trades = connection.execute(
                    """
                    SELECT action, price, notional, fee, signal_payload
                    FROM paper_trade
                    ORDER BY paper_trade_id
                    """
                ).fetchall()

        self.assertEqual(len(results), 2)
        self.assertEqual(len(positions), 2)
        self.assertEqual([float(row["entry_notional"]) for row in positions], [3000.0, 5000.0])
        self.assertAlmostEqual(float(positions[0]["quantity"]), 3000.0 / 64000.0)
        self.assertAlmostEqual(float(positions[1]["quantity"]), 5000.0 / 63950.0)
        self.assertEqual([row["action"] for row in trades], ["open_long", "open_long"])
        self.assertEqual([float(row["notional"]) for row in trades], [3000.0, 5000.0])
        self.assertIn("entry_order_slices", str(trades[0]["signal_payload"]))

    def test_paper_strategy_closes_profitable_positions_and_opposite_signal_closes_all(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                evaluate_pin_paper_strategy_events(
                    connection,
                    market="CRYPTO_FUTURES",
                    symbol="BTCUSDT",
                    stage_event={
                        "timestamp": "2026-04-12T13:21:05Z",
                        "stage": "rebound_confirmed",
                        "direction": "down_flush",
                        "price": 64000.0,
                        "entry_order_slices": [
                            {"trigger": "rebound_confirmed", "price": 64000.0, "notional": 3000.0},
                            {"trigger": "rebound_confirmed", "price": 64100.0, "notional": 4000.0},
                        ],
                    },
                )
                profit_results = evaluate_pin_paper_strategy_events(
                    connection,
                    market="CRYPTO_FUTURES",
                    symbol="BTCUSDT",
                    stage_event={
                        "timestamp": "2026-04-12T13:23:05Z",
                        "stage": "normal",
                        "direction": "down_flush",
                        "price": 64400.0,
                    },
                )
                opposite_results = evaluate_pin_paper_strategy_events(
                    connection,
                    market="CRYPTO_FUTURES",
                    symbol="BTCUSDT",
                    stage_event={
                        "timestamp": "2026-04-12T13:25:05Z",
                        "stage": "rebound_confirmed",
                        "direction": "up_squeeze",
                        "price": 64200.0,
                    },
                )
                rows = connection.execute(
                    """
                    SELECT status, exit_price, realized_pnl
                    FROM paper_position
                    ORDER BY paper_position_id
                    """
                ).fetchall()

        self.assertEqual([result.action for result in profit_results], ["close_long"])
        self.assertEqual([result.action for result in opposite_results], ["close_long"])
        self.assertEqual([row["status"] for row in rows], ["closed", "closed"])
        self.assertGreater(float(rows[0]["realized_pnl"]), 0.0)
        self.assertIsNotNone(rows[1]["realized_pnl"])

    def test_live_candidate_signal_scorer_blocks_weak_realtime_entry(self):
        weak_down_wick = {
            "config": {"positive_stage": "down_wick"},
            "thresholds": {
                "5s": {
                    "window_seconds": 5.0,
                    "shock_move_pct": 0.004,
                    "notional_threshold": 500_000.0,
                    "imbalance_threshold": 0.50,
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            engine = RealtimePinPaperStrategyEngine(
                candidate_signal_scorer=CandidateSignalScorer(
                    down_wick=weak_down_wick,
                    min_candidate_score=1.0,
                )
            )

            with connect(db_path) as connection:
                results = engine._apply_stage_event(
                    connection,
                    _stage_event(
                        stage=Stage.REBOUND_CONFIRMED,
                        direction="down_flush",
                        price=100.0,
                        trade_windows={
                            "5s": {
                                "price_move_pct": -0.001,
                                "total_notional": 50_000.0,
                                "trade_imbalance": -0.10,
                            }
                        },
                    ),
                )
                trade_count = connection.execute("SELECT count(*) FROM paper_trade").fetchone()[0]

        self.assertEqual(results, [])
        self.assertEqual(trade_count, 0)

    def test_live_kline_curve_candidate_creates_independent_entry_signal(self):
        ts = 1_777_000_000.0
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            engine = RealtimePinPaperStrategyEngine(
                config=PinStageConfig(
                    min_entry_order_notional=1_000.0,
                    max_entry_order_notional=2_000.0,
                    entry_order_curve_gamma=1.0,
                ),
                kline_curve_candidate_config=KlineCurveCandidateConfig(
                    windows_minutes=(1,),
                    min_curve_score=0.50,
                    lookahead_seconds=60.0,
                    min_cluster_score=0.0,
                ),
            )
            engine.on_kline_bar(
                "BTCUSDT",
                {
                    "timestamp": ts,
                    "open": 100.0,
                    "high": 100.1,
                    "low": 99.9,
                    "close": 100.0,
                    "volume": 10.0,
                    "quote_volume": 1000.0,
                },
            )
            engine.on_kline_bar(
                "BTCUSDT",
                {
                    "timestamp": ts + 60.0,
                    "open": 100.0,
                    "high": 100.2,
                    "low": 98.0,
                    "close": 99.5,
                    "volume": 100.0,
                    "quote_volume": 9900.0,
                },
            )

            with connect(db_path) as connection:
                results = engine._apply_stage_event(
                    connection,
                    _stage_event(
                        timestamp=ts + 121.0,
                        stage=Stage.NORMAL,
                        direction=None,
                        price=98.5,
                        entry_order_plan=None,
                    ),
                )
                trade = connection.execute(
                    """
                    SELECT action, price, notional, signal_payload
                    FROM paper_trade
                    ORDER BY paper_trade_id
                    """
                ).fetchone()

        self.assertEqual([result.action for result in results], ["open_long"])
        self.assertEqual(trade["action"], "open_long")
        self.assertEqual(float(trade["price"]), 98.5)
        self.assertIn("kline_curve", str(trade["signal_payload"]))

    def test_paper_strategy_trade_is_sent_through_mobile_alert_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO push_device (
                        push_token, getui_cid, platform, enabled, created_at_utc, updated_at_utc
                    )
                    VALUES ('getui:cid-1', 'cid-1', 'android', TRUE, ?, ?)
                    """,
                    ("2026-04-12T13:20:00Z", "2026-04-12T13:20:00Z"),
                )
                evaluate_pin_paper_strategy(
                    connection,
                    market="CRYPTO_FUTURES",
                    symbol="ETHUSDT",
                    stage_event={
                        "timestamp": "2026-04-12T13:21:05Z",
                        "stage": "rebound_confirmed",
                        "direction": "down_flush",
                        "price": 3200.0,
                        "strength": 0.61,
                        "trigger_window_seconds": 15.0,
                    },
                )

                messages = evaluate_mobile_alert_rules(connection, "2026-04-12T13:21:06Z")
                event_row = connection.execute(
                    """
                    SELECT
                        mobile_alert_event.triggered_at_utc,
                        mobile_alert_event.message,
                        mobile_alert_event.alert_metadata
                    FROM mobile_alert_event
                    JOIN mobile_alert_rule
                        ON mobile_alert_rule.mobile_alert_rule_id =
                            mobile_alert_event.mobile_alert_rule_id
                    WHERE mobile_alert_rule.source_type = 'strategy'
                    """
                ).fetchone()

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["symbol"], "ETHUSDT")
        self.assertEqual(messages[0]["data"]["period"], "1m")
        event_metadata = json.loads(event_row["alert_metadata"])
        self.assertEqual(event_metadata["chart_period"], "1m")
        self.assertEqual(event_metadata["signal_timeframe"], "realtime")
        self.assertNotIn("period", event_metadata)
        self.assertEqual(event_row["triggered_at_utc"], "2026-04-12T13:21:05Z")
        self.assertIn("模拟开多", str(messages[0]["body"]))
        self.assertIn("crypto_pin_rebound_v1", str(event_row["alert_metadata"]))

    def test_realtime_pin_engine_turns_live_depth_and_trades_into_paper_trade(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                engine = RealtimePinPaperStrategyEngine(
                    symbols=["BTCUSDT"],
                    config_overrides={
                        "feature_window_seconds": (1.0,),
                        "trigger_window_seconds": (1.0,),
                        "window_seconds": 1.0,
                        "min_window_trades": 2,
                        "pre_shock_move_pct": 0.0005,
                        "shock_move_pct_by_window": (),
                        "extreme_move_pct_by_window": (),
                        "shock_move_pct": 0.001,
                        "extreme_move_pct": 0.002,
                        "min_trade_notional": 10.0,
                        "rebound_start_ratio": 0.2,
                        "rebound_confirm_ratio": 0.5,
                    },
                )
                engine.on_depth(
                    connection,
                    {
                        "e": "depthUpdate",
                        "E": 1_776_000_000_000,
                        "T": 1_776_000_000_000,
                        "s": "BTCUSDT",
                        "b": [["99.90", "100"]],
                        "a": [["100.10", "100"]],
                    },
                )
                for index, price in enumerate([100.0, 99.7, 99.5, 99.8]):
                    engine.on_trade(
                        connection,
                        {
                            "e": "aggTrade",
                            "E": 1_776_000_000_000 + index * 100,
                            "T": 1_776_000_000_000 + index * 100,
                            "s": "BTCUSDT",
                            "p": str(price),
                            "q": "1.0",
                            "m": index in {1, 2},
                        },
                    )
                trade_count = connection.execute("SELECT count(*) FROM paper_trade").fetchone()[0]
                position = connection.execute(
                    "SELECT status, entry_price FROM paper_position WHERE symbol = 'BTCUSDT'"
                ).fetchone()

        self.assertEqual(trade_count, 1)
        self.assertEqual(position["status"], "open")
        self.assertEqual(position["entry_price"], 99.8)

    def test_mobile_strategy_payload_lists_running_pin_strategy_and_positions(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                evaluate_pin_paper_strategy(
                    connection,
                    market="CRYPTO_FUTURES",
                    symbol="BTCUSDT",
                    stage_event={
                        "timestamp": "2026-04-12T13:21:05Z",
                        "stage": "rebound_confirmed",
                        "direction": "down_flush",
                        "price": 64000.0,
                        "strength": 0.72,
                        "trigger_window_seconds": 15.0,
                    },
                )
                payload = get_mobile_strategy_payload(connection)

        self.assertEqual(payload["strategies"][0]["strategy_id"], "crypto_pin_rebound_v1")
        self.assertEqual(payload["strategies"][0]["symbols"][0]["symbol"], "BTCUSDT")
        self.assertEqual(payload["strategies"][0]["symbols"][0]["position_status"], "open")
        self.assertEqual(payload["strategies"][0]["symbols"][0]["entry_price"], 64000.0)

    def test_mobile_strategy_payload_lists_configured_pin_symbols_before_trades(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                payload = get_mobile_strategy_payload(connection)

        self.assertEqual(payload["strategies"][0]["strategy_id"], "crypto_pin_rebound_v1")
        self.assertTrue(payload["strategies"][0]["enabled"])
        self.assertEqual(
            [item["symbol"] for item in payload["strategies"][0]["symbols"]],
            ["BTCUSDT", "ETHUSDT"],
        )
        self.assertEqual(
            [item["position_status"] for item in payload["strategies"][0]["symbols"]],
            ["none", "none"],
        )

    def test_futures_trade_book_collector_archives_raw_messages_by_day(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            archive_dir = Path(tmp_dir) / "ws-archive"
            init_database(db_path)
            collector = BinanceFuturesTradeBookWebSocketCollector(
                db_path=db_path,
                symbols=["BTCUSDT"],
                archive_dir=archive_dir,
            )
            message = json.dumps(
                {
                    "stream": "btcusdt@aggTrade",
                    "data": {
                        "e": "aggTrade",
                        "E": 1_776_000_000_111,
                        "T": 1_776_000_000_100,
                        "s": "BTCUSDT",
                        "p": "64000.50",
                        "q": "2.25",
                        "m": True,
                    },
                }
            )

            collector._on_message(None, message)

            archive_path = archive_dir / "2026-04-12.jsonl.gz"
            with gzip.open(archive_path, "rt", encoding="utf-8") as file:
                archived = json.loads(file.readline())

        self.assertEqual(archived["stream"], "btcusdt@aggTrade")
        self.assertEqual(archived["data"]["s"], "BTCUSDT")

    def test_futures_trade_book_collector_delivers_push_after_paper_trade(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            delivered = []

            class FakeEngine:
                def on_trade(self, connection, payload):
                    return PaperStrategyResult(
                        strategy_id="crypto_pin_rebound_v1",
                        market="CRYPTO_FUTURES",
                        symbol="BTCUSDT",
                        action="open_long",
                        price=64000.0,
                        realized_return_pct=None,
                        message="BTCUSDT Pin 模拟开多",
                        event_time_utc="2026-04-12T13:21:05Z",
                    )

                def on_depth(self, connection, payload):
                    return None

            collector = BinanceFuturesTradeBookWebSocketCollector(
                db_path=db_path,
                symbols=["BTCUSDT"],
                engine=FakeEngine(),
                push_deliverer=lambda connection, now_utc: delivered.append(now_utc) or [],
            )

            collector._on_message(
                None,
                json.dumps(
                    {
                        "stream": "btcusdt@aggTrade",
                        "data": {
                            "e": "aggTrade",
                            "E": 1_776_000_000_111,
                            "T": 1_776_000_000_100,
                            "s": "BTCUSDT",
                            "p": "64000.50",
                            "q": "2.25",
                            "m": True,
                        },
                    }
                ),
            )

        self.assertEqual(delivered, ["2026-04-12T13:21:05Z"])

    def test_futures_trade_book_collector_delivers_push_for_each_paper_trade_result(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            delivered = []

            class FakeEngine:
                def on_trade(self, connection, payload):
                    return [
                        PaperStrategyResult(
                            strategy_id="crypto_pin_rebound_v1",
                            market="CRYPTO_FUTURES",
                            symbol="BTCUSDT",
                            action="open_long",
                            price=64000.0,
                            realized_return_pct=None,
                            message="BTCUSDT Pin 模拟开多 1",
                            event_time_utc="2026-04-12T13:21:05Z",
                        ),
                        PaperStrategyResult(
                            strategy_id="crypto_pin_rebound_v1",
                            market="CRYPTO_FUTURES",
                            symbol="BTCUSDT",
                            action="open_long",
                            price=63950.0,
                            realized_return_pct=None,
                            message="BTCUSDT Pin 模拟开多 2",
                            event_time_utc="2026-04-12T13:21:05Z",
                        ),
                    ]

                def on_depth(self, connection, payload):
                    return None

            collector = BinanceFuturesTradeBookWebSocketCollector(
                db_path=db_path,
                symbols=["BTCUSDT"],
                engine=FakeEngine(),
                push_deliverer=lambda connection, now_utc: delivered.append(now_utc) or [],
            )

            collector._on_message(
                None,
                json.dumps(
                    {
                        "stream": "btcusdt@aggTrade",
                        "data": {
                            "e": "aggTrade",
                            "E": 1_776_000_000_111,
                            "T": 1_776_000_000_100,
                            "s": "BTCUSDT",
                            "p": "64000.50",
                            "q": "2.25",
                            "m": True,
                        },
                    }
                ),
            )

        self.assertEqual(delivered, ["2026-04-12T13:21:05Z", "2026-04-12T13:21:05Z"])


def _stage_event(
    *,
    timestamp: float = 1_777_000_000.0,
    stage: Stage = Stage.REBOUND_CONFIRMED,
    direction: str | None = "down_flush",
    price: float = 100.0,
    entry_order_plan: EntryOrderPlan | None = None,
    trade_windows: dict[str, dict[str, float]] | None = None,
) -> StageEvent:
    return StageEvent(
        timestamp=timestamp,
        symbol="BTCUSDT",
        stage=stage,
        direction=direction,  # type: ignore[arg-type]
        price=price,
        size=1.0,
        side="Sell",
        started_at=timestamp - 5.0,
        ended_at=None,
        entry_candidate_price=price,
        strength=0.7,
        trade_notional=100_000.0,
        trade_imbalance=-0.2,
        book_imbalance=0.1,
        depth_drop=0.0,
        rebound_ratio=0.6,
        trade_count=10,
        buy_notional=10_000.0,
        sell_notional=90_000.0,
        trigger_window_seconds=5.0,
        trade_windows=trade_windows
        or {
            "5s": {
                "price_move_pct": -0.005,
                "total_notional": 150_000.0,
                "trade_imbalance": -0.2,
            }
        },
        entry_order_plan=entry_order_plan
        or EntryOrderPlan(
            entry_side="long",
            order_action="buy",
            allocation_ratio=0.5,
            total_notional=2_000.0,
            slices=(
                {
                    "index": 1,
                    "trigger": "rebound_confirmed",
                    "price": price,
                    "notional": 2_000.0,
                },
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
