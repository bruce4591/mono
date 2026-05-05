from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from market.db import connect, init_database


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


if __name__ == "__main__":
    unittest.main()
