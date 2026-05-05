from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.data_integrity import sqlite_table_counts
from market.db import connect, init_database


class DataIntegrityTests(unittest.TestCase):
    def test_sqlite_table_counts_include_core_tables(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            with connect(db_path) as connection:
                counts = sqlite_table_counts(connection, ["instrument", "push_device"])

        self.assertEqual(counts["instrument"], 0)
        self.assertEqual(counts["push_device"], 0)


if __name__ == "__main__":
    unittest.main()
