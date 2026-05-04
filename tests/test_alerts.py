from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.alerts import AlertEvaluationResult, evaluate_alert_rules, evaluate_mobile_alert_rules
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
        self.assertIn("mobile_alert_rule", tables)
        self.assertIn("mobile_alert_event", tables)

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

    def test_evaluate_mobile_alert_rules_triggers_price_above(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                _insert_mobile_alert_fixture(
                    connection,
                    condition_type="price_above",
                    threshold=64000.0,
                    last_price=65000.0,
                    change_pct=1.5,
                )
                messages = evaluate_mobile_alert_rules(
                    connection,
                    now_utc="2026-05-04T03:00:00Z",
                )
                events = connection.execute(
                    """
                    SELECT observed_value, message, delivery_status
                    FROM mobile_alert_event
                    """
                ).fetchall()

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["push_token"], "ExponentPushToken[test-token]")
        self.assertEqual(messages[0]["title"], "BTCUSDT 价格提醒")
        self.assertEqual(messages[0]["data"]["market"], "CRYPTO")
        self.assertEqual(messages[0]["data"]["symbol"], "BTCUSDT")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["observed_value"], 65000.0)
        self.assertEqual(events[0]["delivery_status"], "pending")
        self.assertEqual(events[0]["message"], "BTCUSDT last_price 65000.0 > 64000.0")

    def test_evaluate_mobile_alert_rules_triggers_price_below(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                _insert_mobile_alert_fixture(
                    connection,
                    condition_type="price_below",
                    threshold=66000.0,
                    last_price=65000.0,
                    change_pct=-1.2,
                )
                messages = evaluate_mobile_alert_rules(
                    connection,
                    now_utc="2026-05-04T03:00:00Z",
                )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["body"], "BTCUSDT last_price 65000.0 < 66000.0")

    def test_evaluate_mobile_alert_rules_respects_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                rule_id = _insert_mobile_alert_fixture(
                    connection,
                    condition_type="change_pct_above",
                    threshold=1.0,
                    last_price=65000.0,
                    change_pct=2.0,
                    cooldown_seconds=900,
                )
                connection.execute(
                    """
                    INSERT INTO mobile_alert_event (
                        mobile_alert_rule_id,
                        triggered_at_utc,
                        observed_value,
                        message,
                        delivery_status
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        rule_id,
                        "2026-05-04T02:50:01Z",
                        1.5,
                        "previous",
                        "sent",
                    ),
                )
                messages = evaluate_mobile_alert_rules(
                    connection,
                    now_utc="2026-05-04T03:00:00Z",
                )
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM mobile_alert_event"
                ).fetchone()[0]

        self.assertEqual(messages, [])
        self.assertEqual(event_count, 1)

    def test_evaluate_mobile_alert_rules_skips_disabled_rules(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                _insert_mobile_alert_fixture(
                    connection,
                    condition_type="change_pct_below",
                    threshold=-1.0,
                    last_price=65000.0,
                    change_pct=-2.0,
                    enabled=False,
                )
                messages = evaluate_mobile_alert_rules(
                    connection,
                    now_utc="2026-05-04T03:00:00Z",
                )
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM mobile_alert_event"
                ).fetchone()[0]

        self.assertEqual(messages, [])
        self.assertEqual(event_count, 0)


def _insert_mobile_alert_fixture(
    connection,
    *,
    condition_type: str,
    threshold: float,
    last_price: float,
    change_pct: float,
    cooldown_seconds: int = 900,
    enabled: bool = True,
) -> int:
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
            snapshot_ts_utc="2026-05-04T02:59:00Z",
            trade_date_local="2026-05-04",
            last_price=last_price,
            change_pct=change_pct,
            volume_raw=120.0,
            turnover_raw=7_800_000.0,
            quote_currency="USDT",
            source="test",
        )
    )
    connection.execute(
        """
        INSERT INTO push_device (
            push_token,
            platform,
            device_label,
            enabled,
            created_at_utc,
            updated_at_utc
        )
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (
            "ExponentPushToken[test-token]",
            "android",
            "OnePlus 13T",
            "2026-05-04T02:58:00Z",
            "2026-05-04T02:58:00Z",
        ),
    )
    push_device_id = connection.execute(
        "SELECT push_device_id FROM push_device WHERE push_token = ?",
        ("ExponentPushToken[test-token]",),
    ).fetchone()["push_device_id"]
    connection.execute(
        """
        INSERT INTO mobile_alert_rule (
            push_device_id,
            symbol,
            market,
            condition_type,
            threshold,
            cooldown_seconds,
            enabled,
            created_at_utc,
            updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            push_device_id,
            "BTCUSDT",
            "CRYPTO",
            condition_type,
            threshold,
            cooldown_seconds,
            int(enabled),
            "2026-05-04T02:58:00Z",
            "2026-05-04T02:58:00Z",
        ),
    )
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
