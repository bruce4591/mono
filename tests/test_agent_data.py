from __future__ import annotations

import builtins
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from market.agent_data import query_symbol_history
from market.db import connect, init_database
from market.models import Instrument, IntradayBar
from market.parquet_export import export_bars_to_parquet
from market.repositories import InstrumentRepository, IntradayBarRepository


class AgentDataTests(unittest.TestCase):
    def test_query_symbol_history_reads_exported_intraday_parquet(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            lake_root = Path(tmp_dir) / "lake"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = InstrumentRepository(connection).upsert(
                    Instrument(
                        market="HK",
                        symbol="00700",
                        display_name="Tencent",
                        exchange="HKEX",
                        instrument_type="stock",
                        quote_currency="HKD",
                        timezone="Asia/Hong_Kong",
                    )
                )
                IntradayBarRepository(connection).upsert(
                    IntradayBar(
                        instrument_id=instrument_id,
                        interval="1m",
                        bar_start_ts_utc="2026-05-05T01:30:00Z",
                        bar_end_ts_utc="2026-05-05T01:31:00Z",
                        trade_date_local="2026-05-05",
                        open=501.0,
                        high=502.0,
                        low=500.5,
                        close=501.5,
                        volume_raw=10000.0,
                        turnover_raw=5015000.0,
                        is_closed_bar=True,
                        source="test",
                    )
                )
                export_bars_to_parquet(connection, lake_root=lake_root)

            rows = query_symbol_history(
                lake_root=lake_root,
                market="HK",
                asset_class="stock",
                symbol="00700",
                interval="1m",
                start_ts_utc="2026-05-05T00:00:00Z",
                end_ts_utc="2026-05-06T00:00:00Z",
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "00700")
        self.assertEqual(rows[0]["bar_start_ts_utc"], "2026-05-05T01:30:00Z")
        self.assertEqual(rows[0]["close"], 501.5)

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
