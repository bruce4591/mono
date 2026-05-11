from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from datetime import UTC, datetime, timedelta

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
        self.assertIn("indicator_definition", tables)
        self.assertIn("indicator_value", tables)

    def test_mobile_delivery_tables_are_created_by_schema(self):
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

        self.assertIn("mobile_alert_delivery", tables)
        self.assertIn("device_checkpoint", tables)
        self.assertIn("device_session", tables)

    def test_mobile_alert_rule_custom_indicator_columns_are_created(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(mobile_alert_rule)"
                    ).fetchall()
                }

        self.assertIn("source_type", columns)
        self.assertIn("metric_key", columns)
        self.assertIn("operator", columns)
        self.assertIn("indicator_id", columns)
        self.assertIn("created_by", columns)

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

    def test_evaluate_mobile_alert_rules_coalesces_recent_events(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                rule_id = _insert_mobile_alert_fixture(
                    connection,
                    condition_type="price_above",
                    threshold=64000.0,
                    last_price=65000.0,
                    change_pct=1.5,
                    cooldown_seconds=0,
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
                        "2026-05-04T02:59:30Z",
                        64100.0,
                        "previous",
                        "sent",
                    ),
                )
                messages = evaluate_mobile_alert_rules(
                    connection,
                    now_utc="2026-05-04T03:00:00Z",
                )
                rows = connection.execute(
                    """
                    SELECT mobile_alert_event_id, triggered_at_utc, observed_value,
                        message, delivery_status
                    FROM mobile_alert_event
                    """
                ).fetchall()

        self.assertEqual(len(messages), 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(messages[0]["mobile_alert_event_id"], rows[0]["mobile_alert_event_id"])
        self.assertEqual(rows[0]["triggered_at_utc"], "2026-05-04T03:00:00Z")
        self.assertEqual(rows[0]["observed_value"], 65000.0)
        self.assertEqual(rows[0]["message"], "BTCUSDT last_price 65000.0 > 64000.0")
        self.assertEqual(rows[0]["delivery_status"], "pending")

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

    def test_evaluate_mobile_alert_rules_triggers_custom_indicator(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                rule_id = _insert_mobile_alert_fixture(
                    connection,
                    condition_type="custom_indicator",
                    threshold=2.5,
                    last_price=65000.0,
                    change_pct=1.5,
                )
                instrument = InstrumentRepository(connection).get_by_market_symbol(
                    "CRYPTO",
                    "BTCUSDT",
                )
                assert instrument is not None
                connection.execute(
                    """
                    INSERT INTO indicator_definition (
                        name,
                        description,
                        expression,
                        input_scope,
                        unit,
                        created_by,
                        enabled,
                        created_at_utc,
                        updated_at_utc
                    )
                    VALUES (?, '', ?, 'instrument', NULL, 'manual', 1, ?, ?)
                    """,
                    (
                        "volume pressure",
                        "volume_raw / max(turnover_raw, 1)",
                        "2026-05-04T02:58:00Z",
                        "2026-05-04T02:58:00Z",
                    ),
                )
                indicator_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
                connection.execute(
                    """
                    UPDATE mobile_alert_rule
                    SET source_type = 'custom_indicator',
                        metric_key = 'indicator_value',
                        operator = '>',
                        indicator_id = ?
                    WHERE mobile_alert_rule_id = ?
                    """,
                    (indicator_id, rule_id),
                )
                connection.execute(
                    """
                    INSERT INTO indicator_value (
                        indicator_id,
                        instrument_id,
                        value_ts_utc,
                        value,
                        input_snapshot,
                        status,
                        created_at_utc
                    )
                    VALUES (?, ?, ?, ?, '{}', 'ok', ?)
                    """,
                    (
                        indicator_id,
                        instrument.instrument_id,
                        "2026-05-04T02:59:00Z",
                        3.1,
                        "2026-05-04T02:59:00Z",
                    ),
                )

                messages = evaluate_mobile_alert_rules(
                    connection,
                    now_utc="2026-05-04T03:00:00Z",
                )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["title"], "BTCUSDT 自定义指标提醒")
        self.assertEqual(messages[0]["body"], "BTCUSDT indicator_value 3.1 > 2.5")

    def test_evaluate_mobile_alert_rules_triggers_crypto_ranking_15m_ma11_breakout_volume(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = _insert_crypto_ranking_alert_fixture(connection)
                _insert_intraday_bars(
                    connection,
                    instrument_id=instrument_id,
                    closes=[100.0] * 20 + [99.0, 103.0],
                    volumes=[100.0] * 21 + [200.0],
                )

                messages = evaluate_mobile_alert_rules(
                    connection,
                    now_utc="2026-05-04T05:31:00Z",
                )
                events = connection.execute(
                    """
                    SELECT
                        mobile_alert_event.message,
                        mobile_alert_event.alert_metadata,
                        mobile_alert_rule.condition_type
                    FROM mobile_alert_event
                    JOIN mobile_alert_rule
                        ON mobile_alert_rule.mobile_alert_rule_id =
                            mobile_alert_event.mobile_alert_rule_id
                    """
                ).fetchall()

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["title"], "BTCUSDT 15m MA11 突破")
        self.assertIn("量比 2.00x", messages[0]["body"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["condition_type"], "ma11_breakout_volume_15m")
        self.assertIn("15m MA11 突破", events[0]["message"])
        self.assertIn('"condition_label":"15m MA11 突破 + 量比 >= 1.5x"', events[0]["alert_metadata"])

    def test_evaluate_mobile_alert_rules_deduplicates_crypto_ranking_15m_bar(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = _insert_crypto_ranking_alert_fixture(connection)
                _insert_intraday_bars(
                    connection,
                    instrument_id=instrument_id,
                    closes=[100.0] * 20 + [99.0, 103.0],
                    volumes=[100.0] * 21 + [200.0],
                )

                first_messages = evaluate_mobile_alert_rules(
                    connection,
                    now_utc="2026-05-04T05:31:00Z",
                )
                second_messages = evaluate_mobile_alert_rules(
                    connection,
                    now_utc="2026-05-04T05:32:00Z",
                )
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM mobile_alert_event"
                ).fetchone()[0]

        self.assertEqual(len(first_messages), 1)
        self.assertEqual(second_messages, [])
        self.assertEqual(event_count, 1)

    def test_evaluate_mobile_alert_rules_triggers_crypto_ranking_15m_ma11_breakdown(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = _insert_crypto_ranking_alert_fixture(connection)
                _insert_intraday_bars(
                    connection,
                    instrument_id=instrument_id,
                    closes=[100.0] * 20 + [101.0, 97.0],
                    volumes=[100.0] * 22,
                )

                messages = evaluate_mobile_alert_rules(
                    connection,
                    now_utc="2026-05-04T05:31:00Z",
                )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["title"], "BTCUSDT 15m MA11 跌破")
        self.assertIn("15m MA11 跌破", messages[0]["body"])

    def test_evaluate_mobile_alert_rules_triggers_crypto_ranking_1d_ma11_once_per_day(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = _insert_crypto_ranking_alert_fixture(connection)
                _insert_daily_bars(
                    connection,
                    instrument_id=instrument_id,
                    closes=[100.0] * 10 + [99.0, 103.0],
                )

                first_messages = evaluate_mobile_alert_rules(
                    connection,
                    now_utc="2026-05-04T23:01:00Z",
                )
                second_messages = evaluate_mobile_alert_rules(
                    connection,
                    now_utc="2026-05-04T23:30:00Z",
                )
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM mobile_alert_event"
                ).fetchone()[0]

        self.assertEqual(len(first_messages), 1)
        self.assertEqual(first_messages[0]["title"], "BTCUSDT 1d MA11 突破")
        self.assertEqual(second_messages, [])
        self.assertEqual(event_count, 1)


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


def _insert_crypto_ranking_alert_fixture(connection) -> int:
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
    connection.execute(
        """
        INSERT INTO ranking_snapshot (
            board_name,
            snapshot_ts_utc,
            rank,
            instrument_id,
            turnover_raw,
            quote_currency,
            change_pct,
            source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "CRYPTO_TURNOVER_TOP50",
            "2026-05-04T05:30:00Z",
            1,
            instrument_id,
            1000000.0,
            "USDT",
            3.2,
            "test",
        ),
    )
    return instrument_id


def _insert_intraday_bars(
    connection,
    *,
    instrument_id: int,
    closes: list[float],
    volumes: list[float],
) -> None:
    start = datetime(2026, 5, 4, 0, 0, tzinfo=UTC)
    for index, (close, volume) in enumerate(zip(closes, volumes, strict=True)):
        bar_start = start + timedelta(minutes=15 * index)
        bar_end = bar_start + timedelta(minutes=15)
        connection.execute(
            """
            INSERT INTO bar_intraday (
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
            )
            VALUES (?, '15m', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'test')
            """,
            (
                instrument_id,
                _format_test_utc(bar_start),
                _format_test_utc(bar_end),
                bar_start.date().isoformat(),
                close,
                close,
                close,
                close,
                volume,
                volume * close,
            ),
        )


def _insert_daily_bars(connection, *, instrument_id: int, closes: list[float]) -> None:
    start = datetime(2026, 4, 23, tzinfo=UTC)
    for index, close in enumerate(closes):
        trade_date = (start + timedelta(days=index)).date().isoformat()
        connection.execute(
            """
            INSERT INTO bar_daily (
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
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'USDT', 'test')
            """,
            (
                instrument_id,
                trade_date,
                close,
                close,
                close,
                close,
                1000.0,
                1000.0 * close,
            ),
        )


def _format_test_utc(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    unittest.main()
