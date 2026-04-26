from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.binance import (
    binance_symbol_to_instrument,
    parse_binance_kline,
    sync_binance_klines,
)
from market.db import connect, init_database
from market.models import IntradayBar
from market.repositories import InstrumentRepository, IntradayBarRepository


class BinanceTests(unittest.TestCase):
    def test_parse_binance_kline_uses_quote_volume_as_turnover(self):
        bar = parse_binance_kline(
            instrument_id=7,
            interval="15m",
            row=[
                1_776_000_000_000,
                "64000.10",
                "64200.20",
                "63950.30",
                "64100.40",
                "12.5",
                1_776_000_899_999,
                "801255.00",
            ],
            now_ms=1_776_001_000_000,
            timezone_name="UTC",
        )

        self.assertEqual(bar.instrument_id, 7)
        self.assertEqual(bar.interval, "15m")
        self.assertEqual(bar.bar_start_ts_utc, "2026-04-12T13:20:00Z")
        self.assertEqual(bar.bar_end_ts_utc, "2026-04-12T13:35:00Z")
        self.assertEqual(bar.trade_date_local, "2026-04-12")
        self.assertEqual(bar.volume_raw, 12.5)
        self.assertEqual(bar.turnover_raw, 801255.0)
        self.assertTrue(bar.is_closed_bar)
        self.assertEqual(bar.source, "binance")

    def test_binance_symbol_to_instrument_sets_crypto_metadata(self):
        instrument = binance_symbol_to_instrument("BTCUSDT")

        self.assertEqual(instrument.market, "CRYPTO")
        self.assertEqual(instrument.symbol, "BTCUSDT")
        self.assertEqual(instrument.display_name, "BTC/USDT")
        self.assertEqual(instrument.exchange, "BINANCE")
        self.assertEqual(instrument.instrument_type, "crypto")
        self.assertEqual(instrument.quote_currency, "USDT")
        self.assertEqual(instrument.timezone, "UTC")

    def test_sync_binance_klines_upserts_instrument_bars_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            def fetcher(symbol: str, interval: str, limit: int) -> list[list[object]]:
                self.assertEqual(symbol, "BTCUSDT")
                self.assertEqual(interval, "15m")
                self.assertEqual(limit, 2)
                return [
                    [
                        1_776_000_000_000,
                        "64000",
                        "64200",
                        "63900",
                        "64100",
                        "10",
                        1_776_000_899_999,
                        "641000",
                    ],
                    [
                        1_776_000_900_000,
                        "64100",
                        "64300",
                        "64000",
                        "64250",
                        "12",
                        1_776_001_799_999,
                        "771000",
                    ],
                ]

            with connect(db_path) as connection:
                instrument_id = InstrumentRepository(connection).upsert(
                    binance_symbol_to_instrument("BTCUSDT")
                )
                IntradayBarRepository(connection).upsert(
                    IntradayBar(
                        instrument_id=instrument_id,
                        interval="15m",
                        bar_start_ts_utc="2026-04-24T00:00:00Z",
                        bar_end_ts_utc="2026-04-24T00:15:00Z",
                        trade_date_local="2026-04-24",
                        open=1.0,
                        high=1.0,
                        low=1.0,
                        close=1.0,
                        volume_raw=1.0,
                        turnover_raw=1.0,
                        is_closed_bar=True,
                        source="sample",
                    )
                )
                result = sync_binance_klines(
                    connection,
                    symbol="BTCUSDT",
                    interval="15m",
                    limit=2,
                    now_ms=1_776_001_900_000,
                    fetcher=fetcher,
                )
                instrument = InstrumentRepository(connection).get_by_market_symbol(
                    "CRYPTO", "BTCUSDT"
                )
                assert instrument is not None
                bars = IntradayBarRepository(connection).list_for_instrument(
                    instrument.instrument_id or 0, "15m"
                )
                snapshot_count = connection.execute(
                    "SELECT count(*) FROM market_snapshot WHERE source = 'binance'"
                ).fetchone()[0]
                change_pct = connection.execute(
                    "SELECT change_pct FROM market_snapshot WHERE source = 'binance'"
                ).fetchone()[0]

        self.assertEqual(result.bars, 2)
        self.assertEqual(result.symbol, "BTCUSDT")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1].close, 64250.0)
        self.assertEqual(snapshot_count, 1)
        self.assertAlmostEqual(change_pct, ((64250.0 - 64100.0) / 64100.0) * 100)


if __name__ == "__main__":
    unittest.main()
