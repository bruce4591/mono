from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.db import connect, init_database
from market.models import Instrument, WatchlistEntry
from market.repositories import InstrumentRepository, WatchlistRepository


class WatchlistRepositoryTests(unittest.TestCase):
    def test_replace_watchlist_deactivates_removed_entries_and_orders_active_entries(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instruments = InstrumentRepository(connection)
                spy_id = instruments.upsert(_etf("SPY", "SPDR S&P 500 ETF"))
                qqq_id = instruments.upsert(_etf("QQQ", "Invesco QQQ"))
                old_id = instruments.upsert(_etf("OLD", "Removed ETF"))

                repository = WatchlistRepository(connection)
                repository.replace(
                    "ETF_FOCUS20",
                    [
                        WatchlistEntry(instrument_id=old_id, sort_order=1),
                    ],
                )
                repository.replace(
                    "ETF_FOCUS20",
                    [
                        WatchlistEntry(instrument_id=qqq_id, sort_order=1),
                        WatchlistEntry(instrument_id=spy_id, sort_order=2),
                    ],
                )
                active = repository.list_active("ETF_FOCUS20")
                old_row = connection.execute(
                    """
                    SELECT is_active
                    FROM watchlist
                    WHERE watchlist_name = ? AND instrument_id = ?
                    """,
                    ("ETF_FOCUS20", old_id),
                ).fetchone()

        self.assertEqual([entry.instrument_id for entry in active], [qqq_id, spy_id])
        self.assertEqual(old_row["is_active"], 0)


def _etf(symbol: str, display_name: str) -> Instrument:
    return Instrument(
        market="US",
        symbol=symbol,
        display_name=display_name,
        exchange="TEST",
        instrument_type="etf",
        quote_currency="USD",
        timezone="America/New_York",
    )


if __name__ == "__main__":
    unittest.main()

