from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.db import connect, init_database
from market.models import Instrument
from market.repositories import InstrumentRepository


class InstrumentRepositoryTests(unittest.TestCase):
    def test_upsert_instrument_inserts_and_updates_by_market_symbol(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                repository = InstrumentRepository(connection)

                first_id = repository.upsert(
                    Instrument(
                        market="CRYPTO",
                        symbol="BTCUSDT",
                        display_name="Bitcoin",
                        exchange="BINANCE",
                        instrument_type="crypto",
                        quote_currency="USDT",
                        timezone="UTC",
                        is_active=True,
                        extra_meta={"source_symbol": "BTCUSDT"},
                    )
                )
                second_id = repository.upsert(
                    Instrument(
                        market="CRYPTO",
                        symbol="BTCUSDT",
                        display_name="Bitcoin / Tether",
                        exchange="BINANCE",
                        instrument_type="crypto",
                        quote_currency="USDT",
                        timezone="UTC",
                        is_active=True,
                        extra_meta={"source_symbol": "BTCUSDT", "base": "BTC"},
                    )
                )
                stored = repository.get_by_market_symbol("CRYPTO", "BTCUSDT")

        self.assertEqual(first_id, second_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.display_name, "Bitcoin / Tether")
        self.assertEqual(stored.extra_meta["base"], "BTC")


if __name__ == "__main__":
    unittest.main()

