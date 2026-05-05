from __future__ import annotations

import builtins
import unittest
from pathlib import Path
from unittest.mock import patch

from market.agent_data import query_symbol_history


class AgentDataTests(unittest.TestCase):
    def test_query_symbol_history_reports_missing_duckdb(self):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "duckdb":
                raise ImportError("missing duckdb")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaisesRegex(RuntimeError, "DuckDB support requires"):
                query_symbol_history(
                    lake_root=Path("/tmp/lake"),
                    market="HK",
                    asset_class="stock",
                    symbol="00700",
                    interval="1m",
                    start_ts_utc="2026-05-05T01:30:00Z",
                    end_ts_utc="2026-05-05T02:30:00Z",
                )


if __name__ == "__main__":
    unittest.main()
