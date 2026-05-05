from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from market.settings import load_settings


class SettingsTests(unittest.TestCase):
    def test_load_settings_uses_local_sqlite_default(self):
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"MARKET_DATABASE_URL", "MARKET_DB_PATH", "MARKET_LOG_LEVEL"}
        }

        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()

        self.assertEqual(settings.db_path, Path("./data/market.sqlite3"))
        self.assertEqual(settings.database_url, "sqlite:///data/market.sqlite3")
        self.assertEqual(settings.log_level, "INFO")

    def test_load_settings_reads_environment(self):
        with patch.dict(
            os.environ,
            {
                "MARKET_DB_PATH": "/tmp/market.sqlite3",
                "MARKET_LOG_LEVEL": "DEBUG",
            },
            clear=True,
        ):
            settings = load_settings()

        self.assertEqual(settings.db_path, Path("/tmp/market.sqlite3"))
        self.assertEqual(settings.database_url, "sqlite:////tmp/market.sqlite3")
        self.assertEqual(settings.log_level, "DEBUG")

    def test_load_settings_accepts_postgres_database_url(self):
        with patch.dict(
            os.environ,
            {
                "MARKET_DATABASE_URL": "postgresql://market:secret@localhost:5432/market",
            },
            clear=True,
        ):
            settings = load_settings()

        self.assertEqual(
            settings.database_url,
            "postgresql://market:secret@localhost:5432/market",
        )
        self.assertIsNone(settings.db_path)


if __name__ == "__main__":
    unittest.main()
