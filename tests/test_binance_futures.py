from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.binance_futures import (
    FUTURES_TRADEFI_WATCHLIST,
    binance_futures_symbol_to_instrument,
    parse_binance_futures_24hr_ticker_snapshot,
    select_futures_tradefi_symbols,
    select_top_futures_usdt_symbols,
)
from market.db import connect, init_database
from market.models import Instrument
from market.repositories import InstrumentRepository


class BinanceFuturesTests(unittest.TestCase):
    def test_futures_instrument_uses_distinct_market_from_spot(self):
        instrument = binance_futures_symbol_to_instrument(
            {
                "symbol": "BTCUSDT",
                "pair": "BTCUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
                "underlyingType": "COIN",
                "underlyingSubType": ["PoW"],
            }
        )

        self.assertEqual(instrument.market, "CRYPTO_FUTURES")
        self.assertEqual(instrument.symbol, "BTCUSDT")
        self.assertEqual(instrument.instrument_type, "crypto_futures")
        self.assertEqual(instrument.quote_currency, "USDT")
        self.assertEqual(instrument.extra_meta["contract_type"], "PERPETUAL")
        self.assertEqual(instrument.extra_meta["underlying_sub_type"], ["PoW"])

    def test_futures_instrument_does_not_collide_with_spot_symbol(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                repository = InstrumentRepository(connection)
                spot_id = repository.upsert(
                    Instrument(
                        market="CRYPTO",
                        symbol="BTCUSDT",
                        display_name="BTCUSDT",
                        exchange="BINANCE",
                        instrument_type="crypto",
                        quote_currency="USDT",
                        timezone="UTC",
                    )
                )
                futures_id = repository.upsert(
                    binance_futures_symbol_to_instrument(
                        {
                            "symbol": "BTCUSDT",
                            "contractType": "PERPETUAL",
                            "status": "TRADING",
                            "quoteAsset": "USDT",
                            "marginAsset": "USDT",
                        }
                    )
                )

        self.assertNotEqual(spot_id, futures_id)

    def test_parse_futures_24hr_snapshot_uses_quote_volume(self):
        instrument = binance_futures_symbol_to_instrument(
            {"symbol": "COINUSDT", "quoteAsset": "USDT"}
        )
        snapshot = parse_binance_futures_24hr_ticker_snapshot(
            instrument_id=7,
            instrument=instrument,
            ticker={
                "symbol": "COINUSDT",
                "lastPrice": "255.50",
                "priceChangePercent": "4.25",
                "volume": "1000",
                "quoteVolume": "255500",
            },
            snapshot_ts_utc="2026-05-03T02:00:00Z",
            trade_date_local="2026-05-03",
        )

        self.assertEqual(snapshot.instrument_id, 7)
        self.assertEqual(snapshot.last_price, 255.5)
        self.assertEqual(snapshot.change_pct, 4.25)
        self.assertEqual(snapshot.volume_raw, 1000.0)
        self.assertEqual(snapshot.turnover_raw, 255500.0)
        self.assertEqual(snapshot.source, "binance_futures_24hr")

    def test_parse_futures_24hr_snapshot_treats_empty_numeric_fields_as_missing(self):
        instrument = binance_futures_symbol_to_instrument(
            {"symbol": "COINUSDT", "quoteAsset": "USDT"}
        )
        snapshot = parse_binance_futures_24hr_ticker_snapshot(
            instrument_id=7,
            instrument=instrument,
            ticker={
                "symbol": "COINUSDT",
                "lastPrice": "",
                "priceChangePercent": "",
                "volume": "",
                "quoteVolume": "",
            },
            snapshot_ts_utc="2026-05-03T02:00:00Z",
            trade_date_local="2026-05-03",
        )

        self.assertIsNone(snapshot.last_price)
        self.assertIsNone(snapshot.change_pct)
        self.assertIsNone(snapshot.volume_raw)
        self.assertIsNone(snapshot.turnover_raw)

    def test_select_top_futures_usdt_symbols_filters_perpetual_trading_usdt(self):
        symbols = select_top_futures_usdt_symbols(
            [
                {"symbol": "OLDUSDT", "quoteVolume": "9999"},
                {"symbol": "BTCUSDT", "quoteVolume": "1000"},
                {"symbol": "ETHUSDT", "quoteVolume": "2000"},
                {"symbol": "BTCUSD_PERP", "quoteVolume": "999999"},
            ],
            {
                "OLDUSDT": {"contractType": "PERPETUAL", "status": "BREAK", "quoteAsset": "USDT"},
                "BTCUSDT": {"contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USDT"},
                "ETHUSDT": {"contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USDT"},
                "BTCUSD_PERP": {"contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USD"},
            },
            limit=2,
        )

        self.assertEqual(symbols, ["ETHUSDT", "BTCUSDT"])

    def test_select_futures_tradefi_symbols_uses_metadata_then_allowlist(self):
        symbols = select_futures_tradefi_symbols(
            [
                {"symbol": "COINUSDT", "quoteVolume": "100"},
                {"symbol": "BTCUSDT", "quoteVolume": "100000"},
                {"symbol": "MSTRUSDT", "quoteVolume": "50"},
            ],
            {
                "COINUSDT": {
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingSubType": ["TradFi"],
                },
                "BTCUSDT": {
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingSubType": ["PoW"],
                },
                "MSTRUSDT": {
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingSubType": [],
                },
            },
            limit=50,
        )

        self.assertEqual(symbols, ["COINUSDT"])

        fallback = select_futures_tradefi_symbols(
            [{"symbol": "MSTRUSDT", "quoteVolume": "50"}],
            {
                "MSTRUSDT": {
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "underlyingSubType": [],
                }
            },
            limit=50,
        )

        self.assertIn("MSTRUSDT", fallback)
        self.assertIn("MSTRUSDT", FUTURES_TRADEFI_WATCHLIST)


if __name__ == "__main__":
    unittest.main()
