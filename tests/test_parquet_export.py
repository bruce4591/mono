from __future__ import annotations

import unittest
from pathlib import Path

from market.parquet_export import parquet_partition_path


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


if __name__ == "__main__":
    unittest.main()
