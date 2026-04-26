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


if __name__ == "__main__":
    unittest.main()

