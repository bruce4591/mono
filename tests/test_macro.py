from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.db import connect, init_database
from market.macro import sync_macro_boards


class MacroTests(unittest.TestCase):
    def test_sync_macro_boards_stores_rates_and_derived_fx_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            with connect(db_path) as connection:
                result = sync_macro_boards(
                    connection,
                    snapshot_ts_utc="2026-05-16T12:00:00Z",
                    bond_rate_frame=[
                        {
                            "日期": "2026-05-15",
                            "美国国债收益率2年": 4.40,
                            "美国国债收益率5年": 4.20,
                            "美国国债收益率10年": 4.50,
                            "美国国债收益率30年": 4.70,
                            "中国国债收益率10年": 1.75,
                            "中国国债收益率30年": 2.05,
                        },
                        {
                            "日期": "2026-05-16",
                            "美国国债收益率2年": 4.42,
                            "美国国债收益率5年": 4.23,
                            "美国国债收益率10年": 4.55,
                            "美国国债收益率30年": 4.74,
                            "中国国债收益率10年": 1.77,
                            "中国国债收益率30年": 2.08,
                        },
                    ],
                    fx_safe_frame=[
                        {
                            "日期": "2026-05-15",
                            "美元": 720.00,
                            "欧元": 828.00,
                            "日元": 4.80,
                            "英镑": 960.00,
                            "澳元": 468.00,
                            "加元": 525.00,
                        },
                        {
                            "日期": "2026-05-16",
                            "美元": 721.00,
                            "欧元": 829.15,
                            "日元": 4.85,
                            "英镑": 962.00,
                            "澳元": 470.00,
                            "加元": 524.00,
                        },
                    ],
                )
                rows = connection.execute(
                    """
                    SELECT instrument.market, instrument.symbol, latest_market_snapshot.last_price
                    FROM instrument
                    JOIN latest_market_snapshot
                        ON latest_market_snapshot.instrument_id = instrument.instrument_id
                    WHERE instrument.market IN ('MACRO_RATE', 'FX')
                    ORDER BY instrument.market, instrument.symbol
                    """
                ).fetchall()

        values = {(row["market"], row["symbol"]): row["last_price"] for row in rows}
        self.assertEqual(result.items_synced, 12)
        self.assertAlmostEqual(values[("MACRO_RATE", "US10Y")], 4.55)
        self.assertAlmostEqual(values[("FX", "USDCNY")], 7.21)
        self.assertAlmostEqual(values[("FX", "USDJPY")], 721.00 / 4.85)
        self.assertAlmostEqual(values[("FX", "EURUSD")], 829.15 / 721.00)


if __name__ == "__main__":
    unittest.main()
