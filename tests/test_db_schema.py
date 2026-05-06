from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from market.db import (
    PostgresConnectionAdapter,
    connect,
    create_database_connector,
    init_database,
)


class FakePostgresCursor:
    def __init__(self, rows: list[dict[str, object]] | None = None):
        self.rows = rows or []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakePostgresConnection:
    def __init__(self):
        self.executed: list[tuple[str, object]] = []
        self.committed = False
        self.closed = False

    def execute(self, sql: str, params: object = ()) -> FakePostgresCursor:
        self.executed.append((sql, params))
        return FakePostgresCursor([{"one": 1}])

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class DatabaseSchemaTests(unittest.TestCase):
    def test_init_database_creates_required_tables_and_wal_mode(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"

            init_database(db_path)

            with connect(db_path) as connection:
                journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }

        self.assertEqual(journal_mode, "wal")
        self.assertTrue(
            {
                "instrument",
                "bar_daily",
                "bar_intraday",
                "market_snapshot",
                "ranking_snapshot",
                "watchlist",
                "job_state",
                "source_health",
            }.issubset(table_names)
        )

    def test_init_database_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"

            init_database(db_path)
            init_database(db_path)

            with sqlite3.connect(db_path) as connection:
                instrument_count = connection.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'instrument'"
                ).fetchone()[0]

        self.assertEqual(instrument_count, 1)

    def test_connect_context_manager_closes_connection(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                connection.execute("SELECT 1").fetchone()

            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1").fetchone()

    def test_postgres_adapter_translates_sqlite_placeholders(self):
        fake_connection = FakePostgresConnection()

        with PostgresConnectionAdapter(fake_connection) as connection:
            row = connection.execute(
                "SELECT one FROM example WHERE market = ? AND symbol = ?",
                ("HK", "00700"),
            ).fetchone()

        self.assertEqual(row["one"], 1)
        self.assertEqual(
            fake_connection.executed[0],
            (
                "SELECT one FROM example WHERE market = %s AND symbol = %s",
                ("HK", "00700"),
            ),
        )
        self.assertTrue(fake_connection.committed)
        self.assertTrue(fake_connection.closed)

    def test_postgres_database_connector_reuses_pooled_connection(self):
        fake_connection = FakePostgresConnection()

        from unittest.mock import patch

        with patch("market.db._connect_postgres", return_value=fake_connection) as connect_postgres:
            connector = create_database_connector(
                "postgresql://market_app:secret@127.0.0.1:5432/market",
                pool_size=1,
            )

            with connector() as first:
                first.execute("SELECT 1").fetchone()
            with connector() as second:
                second.execute("SELECT 1").fetchone()

        self.assertEqual(connect_postgres.call_count, 1)
        self.assertFalse(fake_connection.closed)
        self.assertEqual(len(fake_connection.executed), 2)

    def test_schema_migrations_are_recorded(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            with connect(db_path) as connection:
                rows = connection.execute(
                    """
                    SELECT migration_id
                    FROM schema_migration
                    ORDER BY migration_id
                    """
                ).fetchall()

        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["migration_id"]), "0001_initial_schema")

    def test_latest_and_history_snapshot_tables_exist(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            with connect(db_path) as connection:
                tables = {
                    str(row["name"])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }

        self.assertIn("latest_market_snapshot", tables)
        self.assertIn("market_snapshot_history", tables)

    def test_snapshot_split_migration_backfills_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            with connect(db_path) as connection:
                instrument_id = connection.execute(
                    """
                    INSERT INTO instrument (
                        market,
                        symbol,
                        display_name,
                        exchange,
                        instrument_type,
                        quote_currency,
                        timezone
                    )
                    VALUES (
                        'HK',
                        '00700',
                        'Tencent',
                        'HKEX',
                        'stock',
                        'HKD',
                        'Asia/Hong_Kong'
                    )
                    RETURNING instrument_id
                    """
                ).fetchone()["instrument_id"]
                connection.execute(
                    """
                    INSERT OR REPLACE INTO market_snapshot (
                        instrument_id,
                        snapshot_ts_utc,
                        trade_date_local,
                        last_price,
                        change_pct,
                        volume_raw,
                        turnover_raw,
                        quote_currency,
                        source
                    )
                    VALUES (
                        ?,
                        '2026-05-05T03:01:04Z',
                        '2026-05-05',
                        468.2,
                        1.2,
                        10,
                        20,
                        'HKD',
                        'akshare'
                    )
                    """,
                    (instrument_id,),
                )
                connection.execute(
                    "DELETE FROM schema_migration WHERE migration_id = '0002_split_market_snapshots'"
                )
                from market.migrations import apply_sqlite_migrations

                apply_sqlite_migrations(connection)
                latest = connection.execute(
                    """
                    SELECT last_price
                    FROM latest_market_snapshot
                    WHERE instrument_id = ?
                    """,
                    (instrument_id,),
                ).fetchone()
                history = connection.execute(
                    """
                    SELECT last_price
                    FROM market_snapshot_history
                    WHERE instrument_id = ?
                    """,
                    (instrument_id,),
                ).fetchone()

        self.assertEqual(float(latest["last_price"]), 468.2)
        self.assertEqual(float(history["last_price"]), 468.2)

    def test_board_refresh_state_table_exists(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            with connect(db_path) as connection:
                row = connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'board_refresh_state'
                    """
                ).fetchone()

        self.assertIsNotNone(row)

    def test_postgres_schema_contains_online_tables(self):
        schema = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "market"
            / "pg_schema.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS schema_migration", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS instrument", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS latest_market_snapshot", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS market_snapshot_history", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS board_refresh_state", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS mobile_alert_delivery", schema)


if __name__ == "__main__":
    unittest.main()
