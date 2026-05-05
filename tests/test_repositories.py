from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.db import connect, init_database
from market.models import Instrument, MarketSnapshot
from market.repositories import InstrumentRepository, MarketSnapshotRepository


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

    def test_upsert_instrument_preserves_existing_extra_meta_when_omitted(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                repository = InstrumentRepository(connection)
                repository.upsert(
                    Instrument(
                        market="CRYPTO",
                        symbol="DOGEUSDT",
                        display_name="DOGE/USDT",
                        exchange="BINANCE",
                        instrument_type="crypto",
                        quote_currency="USDT",
                        timezone="UTC",
                        extra_meta={
                            "base_asset": "DOGE",
                            "quote_asset": "USDT",
                            "price_tick_size": "0.00001000",
                        },
                    )
                )
                repository.upsert(
                    Instrument(
                        market="CRYPTO",
                        symbol="DOGEUSDT",
                        display_name="DOGE/USDT",
                        exchange="BINANCE",
                        instrument_type="crypto",
                        quote_currency="USDT",
                        timezone="UTC",
                        extra_meta={"base_asset": "DOGE", "quote_asset": "USDT"},
                    )
                )
                stored = repository.get_by_market_symbol("CRYPTO", "DOGEUSDT")

        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.extra_meta["price_tick_size"], "0.00001000")

    def test_market_snapshot_upsert_writes_latest_and_history(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            with connect(db_path) as connection:
                instruments = InstrumentRepository(connection)
                instrument_id = instruments.upsert(
                    Instrument(
                        instrument_id=None,
                        market="HK",
                        symbol="00700",
                        display_name="Tencent",
                        exchange="HKEX",
                        instrument_type="stock",
                        quote_currency="HKD",
                        timezone="Asia/Hong_Kong",
                    )
                )
                snapshots = MarketSnapshotRepository(connection)
                snapshots.upsert(
                    MarketSnapshot(
                        instrument_id=instrument_id,
                        snapshot_ts_utc="2026-05-05T03:01:04Z",
                        trade_date_local="2026-05-05",
                        last_price=468.2,
                        change_pct=1.2,
                        volume_raw=10.0,
                        turnover_raw=20.0,
                        quote_currency="HKD",
                        source="akshare",
                    )
                )
                snapshots.upsert(
                    MarketSnapshot(
                        instrument_id=instrument_id,
                        snapshot_ts_utc="2026-05-05T03:02:04Z",
                        trade_date_local="2026-05-05",
                        last_price=469.0,
                        change_pct=1.3,
                        volume_raw=11.0,
                        turnover_raw=22.0,
                        quote_currency="HKD",
                        source="akshare",
                    )
                )
                latest = connection.execute(
                    """
                    SELECT last_price, snapshot_ts_utc
                    FROM latest_market_snapshot
                    WHERE instrument_id = ?
                    """,
                    (instrument_id,),
                ).fetchone()
                history_count = connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM market_snapshot_history
                    WHERE instrument_id = ?
                    """,
                    (instrument_id,),
                ).fetchone()

        self.assertEqual(float(latest["last_price"]), 469.0)
        self.assertEqual(str(latest["snapshot_ts_utc"]), "2026-05-05T03:02:04Z")
        self.assertEqual(int(history_count["count"]), 2)


if __name__ == "__main__":
    unittest.main()
