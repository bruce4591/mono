from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.db import connect, init_database
from market.models import DailyBar, Instrument, IntradayBar
from market.repositories import (
    DailyBarRepository,
    InstrumentRepository,
    IntradayBarRepository,
)


class BarRepositoryTests(unittest.TestCase):
    def test_daily_bar_upsert_updates_by_instrument_and_trade_date(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = InstrumentRepository(connection).upsert(
                    _instrument("US", "AAPL", "Apple")
                )
                repository = DailyBarRepository(connection)

                repository.upsert(
                    DailyBar(
                        instrument_id=instrument_id,
                        trade_date="2026-04-24",
                        open=190.0,
                        high=195.0,
                        low=188.0,
                        close=194.0,
                        volume_raw=1000.0,
                        turnover_raw=194000.0,
                        quote_currency="USD",
                        source="test",
                    )
                )
                repository.upsert(
                    DailyBar(
                        instrument_id=instrument_id,
                        trade_date="2026-04-24",
                        open=191.0,
                        high=196.0,
                        low=189.0,
                        close=195.0,
                        volume_raw=1200.0,
                        turnover_raw=234000.0,
                        quote_currency="USD",
                        source="repair",
                    )
                )
                stored = repository.list_for_instrument(instrument_id)

        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].close, 195.0)
        self.assertEqual(stored[0].turnover_raw, 234000.0)
        self.assertEqual(stored[0].source, "repair")

    def test_intraday_bar_upsert_updates_open_bar_to_closed_bar(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = InstrumentRepository(connection).upsert(
                    _instrument("CRYPTO", "BTCUSDT", "Bitcoin")
                )
                repository = IntradayBarRepository(connection)

                repository.upsert(
                    IntradayBar(
                        instrument_id=instrument_id,
                        interval="15m",
                        bar_start_ts_utc="2026-04-24T00:00:00Z",
                        bar_end_ts_utc="2026-04-24T00:15:00Z",
                        trade_date_local="2026-04-24",
                        open=64000.0,
                        high=64200.0,
                        low=63950.0,
                        close=64100.0,
                        volume_raw=10.0,
                        turnover_raw=641000.0,
                        is_closed_bar=False,
                        source="binance_ws",
                    )
                )
                repository.upsert(
                    IntradayBar(
                        instrument_id=instrument_id,
                        interval="15m",
                        bar_start_ts_utc="2026-04-24T00:00:00Z",
                        bar_end_ts_utc="2026-04-24T00:15:00Z",
                        trade_date_local="2026-04-24",
                        open=64000.0,
                        high=64300.0,
                        low=63900.0,
                        close=64250.0,
                        volume_raw=12.0,
                        turnover_raw=771000.0,
                        is_closed_bar=True,
                        source="binance_ws",
                    )
                )
                stored = repository.list_for_instrument(instrument_id, "15m")

        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].high, 64300.0)
        self.assertTrue(stored[0].is_closed_bar)


def _instrument(market: str, symbol: str, display_name: str) -> Instrument:
    return Instrument(
        market=market,
        symbol=symbol,
        display_name=display_name,
        exchange="TEST",
        instrument_type="crypto" if market == "CRYPTO" else "stock",
        quote_currency="USDT" if market == "CRYPTO" else "USD",
        timezone="UTC",
    )


if __name__ == "__main__":
    unittest.main()

