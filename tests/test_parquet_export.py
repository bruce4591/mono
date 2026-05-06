from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from market.db import connect, init_database
from market.models import DailyBar, Instrument, IntradayBar
from market.parquet_export import export_bars_to_parquet, parquet_partition_path
from market.repositories import DailyBarRepository, InstrumentRepository, IntradayBarRepository


class ParquetExportTests(unittest.TestCase):
    def test_partition_path_for_monthly_intraday_symbol(self):
        path = parquet_partition_path(
            root=Path("/data/market-lake"),
            market="HK",
            asset_class="stock",
            symbol="00700",
            interval="1m",
            year=2026,
            month=5,
        )

        self.assertEqual(
            path,
            Path(
                "/data/market-lake/market=HK/asset_class=stock/symbol=00700/"
                "interval=1m/year=2026/month=05/part-000.parquet"
            ),
        )

    def test_exports_daily_and_intraday_bars_to_partitioned_parquet(self):
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
                DailyBarRepository(connection).upsert(
                    DailyBar(
                        instrument_id=instrument_id,
                        trade_date="2026-05-05",
                        open=500.0,
                        high=510.0,
                        low=499.0,
                        close=508.0,
                        volume_raw=1000000.0,
                        turnover_raw=508000000.0,
                        quote_currency="HKD",
                        source="test",
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

                result = export_bars_to_parquet(connection, lake_root=lake_root)

            daily_path = (
                lake_root
                / "market=HK"
                / "asset_class=stock"
                / "symbol=00700"
                / "interval=1d"
                / "year=2026"
                / "part-000.parquet"
            )
            intraday_path = (
                lake_root
                / "market=HK"
                / "asset_class=stock"
                / "symbol=00700"
                / "interval=1m"
                / "year=2026"
                / "month=05"
                / "part-000.parquet"
            )

            import pyarrow.parquet as pq

            daily_rows = pq.ParquetFile(daily_path).read().to_pylist()
            intraday_rows = pq.ParquetFile(intraday_path).read().to_pylist()

        self.assertEqual(result.files_written, 2)
        self.assertEqual(result.daily_rows, 1)
        self.assertEqual(result.intraday_rows, 1)
        self.assertEqual(daily_rows[0]["symbol"], "00700")
        self.assertEqual(daily_rows[0]["trade_date"], "2026-05-05")
        self.assertEqual(intraday_rows[0]["bar_start_ts_utc"], "2026-05-05T01:30:00Z")
        self.assertTrue(intraday_rows[0]["is_closed_bar"])


if __name__ == "__main__":
    unittest.main()
