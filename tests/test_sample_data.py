from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.db import connect, init_database
from market.repositories import RankingRepository
from market.sample_data import seed_sample_data
from market.watchlists import sync_watchlist_from_file


class SampleDataTests(unittest.TestCase):
    def test_seed_sample_data_writes_snapshots_bars_and_non_empty_rankings(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                sync_watchlist_from_file(connection, Path("config/watchlists/etf_focus20.json"))
                result = seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                ranking = RankingRepository(connection)
                etf_count = ranking.refresh_turnover_board(
                    board_name="ETF_FOCUS20",
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                    market="US",
                    instrument_type="etf",
                    limit=20,
                    watchlist_name="ETF_FOCUS20",
                )
                crypto_count = ranking.refresh_turnover_board(
                    board_name="CRYPTO_TURNOVER_TOP50",
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                    market="CRYPTO",
                    instrument_type="crypto",
                    limit=50,
                )
                spy_daily_count = connection.execute(
                    """
                    SELECT count(*)
                    FROM bar_daily
                    JOIN instrument
                        ON instrument.instrument_id = bar_daily.instrument_id
                    WHERE instrument.symbol = 'SPY'
                    """
                ).fetchone()[0]
                btc_intraday_count = connection.execute(
                    """
                    SELECT count(*)
                    FROM bar_intraday
                    JOIN instrument
                        ON instrument.instrument_id = bar_intraday.instrument_id
                    WHERE instrument.symbol = 'BTCUSDT' AND bar_intraday.interval = '15m'
                    """
                ).fetchone()[0]
                daily_count = connection.execute("SELECT count(*) FROM bar_daily").fetchone()[0]
                intraday_count = connection.execute(
                    "SELECT count(*) FROM bar_intraday"
                ).fetchone()[0]

        self.assertEqual(result.instruments, 5)
        self.assertEqual(result.snapshots, 5)
        self.assertEqual(result.daily_bars, 25)
        self.assertEqual(result.intraday_bars, 30)
        self.assertEqual(daily_count, 25)
        self.assertEqual(intraday_count, 30)
        self.assertEqual(spy_daily_count, 5)
        self.assertEqual(btc_intraday_count, 6)
        self.assertEqual(etf_count, 3)
        self.assertEqual(crypto_count, 2)

    def test_seed_sample_data_removes_stale_sample_bars_when_rerun(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                btc_id = connection.execute(
                    """
                    SELECT instrument_id
                    FROM instrument
                    WHERE market = 'CRYPTO' AND symbol = 'BTCUSDT'
                    """
                ).fetchone()["instrument_id"]
                connection.execute(
                    """
                    INSERT INTO bar_intraday (
                        instrument_id,
                        interval,
                        bar_start_ts_utc,
                        bar_end_ts_utc,
                        trade_date_local,
                        open,
                        high,
                        low,
                        close,
                        volume_raw,
                        turnover_raw,
                        is_closed_bar,
                        source
                    )
                    VALUES (?, '15m', '2026-04-24T19:00:00Z', '2026-04-24T19:15:00Z',
                        '2026-04-24', 1, 1, 1, 1, 1, 1, 1, 'sample')
                    """,
                    (btc_id,),
                )

                seed_sample_data(
                    connection,
                    snapshot_ts_utc="2026-04-24T20:00:00Z",
                    trade_date_local="2026-04-24",
                )
                btc_count = connection.execute(
                    """
                    SELECT count(*)
                    FROM bar_intraday
                    WHERE instrument_id = ? AND interval = '15m'
                    """,
                    (btc_id,),
                ).fetchone()[0]

        self.assertEqual(btc_count, 6)


if __name__ == "__main__":
    unittest.main()
