from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.db import connect, init_database
from market.models import Instrument, MarketSnapshot, WatchlistEntry
from market.repositories import (
    InstrumentRepository,
    MarketSnapshotRepository,
    RankingRepository,
    WatchlistRepository,
)


class RankingRepositoryTests(unittest.TestCase):
    def test_refresh_turnover_board_ranks_market_snapshots_by_turnover(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instruments = InstrumentRepository(connection)
                aapl_id = instruments.upsert(_instrument("US", "AAPL", "stock"))
                msft_id = instruments.upsert(_instrument("US", "MSFT", "stock"))
                btc_id = instruments.upsert(_instrument("CRYPTO", "BTCUSDT", "crypto"))
                snapshots = MarketSnapshotRepository(connection)
                snapshots.upsert(_snapshot(aapl_id, 100_000.0, "USD"))
                snapshots.upsert(_snapshot(msft_id, 300_000.0, "USD"))
                snapshots.upsert(_snapshot(btc_id, 900_000.0, "USDT"))

                repository = RankingRepository(connection)
                created = repository.refresh_turnover_board(
                    board_name="US_STOCK_TURNOVER_TOP50",
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                    market="US",
                    instrument_type="stock",
                    limit=50,
                )
                stored = repository.list_board(
                    "US_STOCK_TURNOVER_TOP50", "2026-04-24T20:00:00Z"
                )

        self.assertEqual(created, 2)
        self.assertEqual([entry.instrument_id for entry in stored], [msft_id, aapl_id])
        self.assertEqual([entry.rank for entry in stored], [1, 2])

    def test_refresh_turnover_board_can_filter_to_active_watchlist(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instruments = InstrumentRepository(connection)
                spy_id = instruments.upsert(_instrument("US", "SPY", "etf"))
                qqq_id = instruments.upsert(_instrument("US", "QQQ", "etf"))
                other_id = instruments.upsert(_instrument("US", "IWM", "etf"))
                WatchlistRepository(connection).replace(
                    "ETF_FOCUS20",
                    [
                        WatchlistEntry(instrument_id=spy_id, sort_order=1),
                        WatchlistEntry(instrument_id=qqq_id, sort_order=2),
                    ],
                )
                snapshots = MarketSnapshotRepository(connection)
                snapshots.upsert(_snapshot(spy_id, 100_000.0, "USD"))
                snapshots.upsert(_snapshot(qqq_id, 200_000.0, "USD"))
                snapshots.upsert(_snapshot(other_id, 900_000.0, "USD"))

                repository = RankingRepository(connection)
                repository.refresh_turnover_board(
                    board_name="ETF_FOCUS20",
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                    market="US",
                    instrument_type="etf",
                    limit=20,
                    watchlist_name="ETF_FOCUS20",
                )
                stored = repository.list_board("ETF_FOCUS20", "2026-04-24T20:00:00Z")

        self.assertEqual([entry.instrument_id for entry in stored], [qqq_id, spy_id])


def _instrument(market: str, symbol: str, instrument_type: str) -> Instrument:
    return Instrument(
        market=market,
        symbol=symbol,
        display_name=symbol,
        exchange="TEST",
        instrument_type=instrument_type,
        quote_currency="USDT" if market == "CRYPTO" else "USD",
        timezone="UTC",
    )


def _snapshot(
    instrument_id: int,
    turnover_raw: float,
    quote_currency: str,
) -> MarketSnapshot:
    return MarketSnapshot(
        instrument_id=instrument_id,
        snapshot_ts_utc="2026-04-24T20:00:00Z",
        trade_date_local="2026-04-24",
        last_price=100.0,
        change_pct=1.5,
        volume_raw=1000.0,
        turnover_raw=turnover_raw,
        quote_currency=quote_currency,
        source="test",
    )


if __name__ == "__main__":
    unittest.main()

