from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.alerts import AlertEvaluationResult, evaluate_alert_rules
from market.db import connect, init_database
from market.models import AlertRule, Instrument, MarketSnapshot
from market.repositories import (
    AlertEventRepository,
    AlertRuleRepository,
    InstrumentRepository,
    MarketSnapshotRepository,
)


class AlertTests(unittest.TestCase):
    def test_alert_tables_are_created_by_schema(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }

        self.assertIn("alert_rule", tables)
        self.assertIn("alert_event", tables)

    def test_alert_rule_repository_upserts_by_name(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                repository = AlertRuleRepository(connection)
                first_id = repository.upsert(
                    AlertRule(
                        name="btc move",
                        market="CRYPTO",
                        symbol="BTCUSDT",
                        metric="change_pct",
                        operator=">=",
                        threshold=1.0,
                    )
                )
                second_id = repository.upsert(
                    AlertRule(
                        name="btc move",
                        market="CRYPTO",
                        symbol="BTCUSDT",
                        metric="change_pct",
                        operator=">=",
                        threshold=2.0,
                    )
                )
                rules = repository.list_active()

        self.assertEqual(first_id, second_id)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].threshold, 2.0)

    def test_evaluate_alert_rules_triggers_from_latest_snapshot_metric(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = InstrumentRepository(connection).upsert(
                    Instrument(
                        market="CRYPTO",
                        symbol="BTCUSDT",
                        display_name="BTC/USDT",
                        exchange="BINANCE",
                        instrument_type="crypto",
                        quote_currency="USDT",
                        timezone="UTC",
                    )
                )
                MarketSnapshotRepository(connection).upsert(
                    MarketSnapshot(
                        instrument_id=instrument_id,
                        snapshot_ts_utc="2026-04-24T20:00:00Z",
                        trade_date_local="2026-04-24",
                        last_price=65000.0,
                        change_pct=2.5,
                        volume_raw=120.0,
                        turnover_raw=7_800_000.0,
                        quote_currency="USDT",
                        source="test",
                    )
                )
                AlertRuleRepository(connection).upsert(
                    AlertRule(
                        name="btc change high",
                        market="CRYPTO",
                        symbol="BTCUSDT",
                        metric="change_pct",
                        operator=">=",
                        threshold=2.0,
                    )
                )
                result = evaluate_alert_rules(
                    connection,
                    triggered_at_utc="2026-04-24T20:01:00Z",
                )
                events = AlertEventRepository(connection).list_recent(limit=10)

        self.assertEqual(result, AlertEvaluationResult(rules_checked=1, events_created=1))
        self.assertEqual(events[0].rule_name, "btc change high")
        self.assertEqual(events[0].observed_value, 2.5)
        self.assertEqual(events[0].message, "BTCUSDT change_pct 2.5 >= 2.0")

    def test_evaluate_alert_rules_uses_registered_metric_resolver(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = InstrumentRepository(connection).upsert(
                    Instrument(
                        market="CRYPTO",
                        symbol="BTCUSDT",
                        display_name="BTC/USDT",
                        exchange="BINANCE",
                        instrument_type="crypto",
                        quote_currency="USDT",
                        timezone="UTC",
                    )
                )
                MarketSnapshotRepository(connection).upsert(
                    MarketSnapshot(
                        instrument_id=instrument_id,
                        snapshot_ts_utc="2026-04-24T20:00:00Z",
                        trade_date_local="2026-04-24",
                        last_price=65000.0,
                        change_pct=0.5,
                        volume_raw=120.0,
                        turnover_raw=7_800_000.0,
                        quote_currency="USDT",
                        source="test",
                    )
                )
                AlertRuleRepository(connection).upsert(
                    AlertRule(
                        name="custom metric",
                        market="CRYPTO",
                        symbol="BTCUSDT",
                        metric="custom_score",
                        operator=">",
                        threshold=10.0,
                    )
                )
                result = evaluate_alert_rules(
                    connection,
                    triggered_at_utc="2026-04-24T20:01:00Z",
                    metric_resolvers={"custom_score": lambda snapshot: 12.0},
                )

        self.assertEqual(result.events_created, 1)


if __name__ == "__main__":
    unittest.main()
