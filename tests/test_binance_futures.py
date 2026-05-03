from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.binance_futures import (
    FUTURES_TRADEFI_WATCHLIST,
    binance_futures_symbol_to_instrument,
    parse_binance_futures_funding_rate,
    parse_binance_futures_kline,
    parse_binance_futures_24hr_ticker_snapshot,
    select_futures_tradefi_symbols,
    select_top_futures_usdt_symbols,
    sync_binance_futures_klines_range,
)
from market.db import connect, init_database
from market.models import Instrument
from market.repositories import InstrumentRepository, IntradayBarRepository


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

    def test_parse_futures_funding_rate_uses_premium_index_payload(self):
        funding = parse_binance_futures_funding_rate(
            {
                "symbol": "ETHUSDT",
                "lastFundingRate": "0.00010000",
                "nextFundingTime": 1777809600000,
                "markPrice": "2301.25",
                "indexPrice": "2300.50",
            }
        )

        self.assertEqual(funding["symbol"], "ETHUSDT")
        self.assertEqual(funding["last_funding_rate"], 0.0001)
        self.assertEqual(funding["last_funding_rate_pct"], 0.01)
        self.assertEqual(funding["next_funding_time_utc"], "2026-05-03T12:00:00Z")
        self.assertEqual(funding["mark_price"], 2301.25)
        self.assertEqual(funding["index_price"], 2300.5)
        self.assertEqual(funding["source"], "binance_futures_premium_index")

    def test_parse_futures_kline_uses_futures_instrument_id(self):
        bar = parse_binance_futures_kline(
            instrument_id=42,
            interval="1m",
            row=[
                1777000000000,
                "2300.10",
                "2308.00",
                "2299.50",
                "2307.25",
                "12.5",
                1777000059999,
                "28840.625",
            ],
            timezone_name="UTC",
        )

        self.assertEqual(bar.instrument_id, 42)
        self.assertEqual(bar.interval, "1m")
        self.assertEqual(bar.bar_start_ts_utc, "2026-04-24T03:06:40Z")
        self.assertEqual(bar.bar_end_ts_utc, "2026-04-24T03:07:40Z")
        self.assertEqual(bar.open, 2300.10)
        self.assertEqual(bar.high, 2308.00)
        self.assertEqual(bar.low, 2299.50)
        self.assertEqual(bar.close, 2307.25)
        self.assertEqual(bar.volume_raw, 12.5)
        self.assertEqual(bar.turnover_raw, 28840.625)
        self.assertEqual(bar.source, "binance_futures")

    def test_parse_futures_kline_treats_empty_numeric_fields_as_missing(self):
        bar = parse_binance_futures_kline(
            instrument_id=42,
            interval="1m",
            row=[
                1777000000000,
                "",
                "",
                "",
                "",
                "",
                1777000059999,
                "",
            ],
            timezone_name="UTC",
        )

        self.assertIsNone(bar.open)
        self.assertIsNone(bar.high)
        self.assertIsNone(bar.low)
        self.assertIsNone(bar.close)
        self.assertIsNone(bar.volume_raw)
        self.assertIsNone(bar.turnover_raw)

    def test_sync_futures_klines_range_writes_crypto_futures_bars(self):
        rows = [
            [
                1777000000000,
                "2300.10",
                "2308.00",
                "2299.50",
                "2307.25",
                "12.5",
                1777000059999,
                "28840.625",
            ]
        ]

        def fake_fetcher(
            symbol: str,
            interval: str,
            start_time_ms: int,
            end_time_ms: int,
            limit: int,
        ) -> list[list[object]]:
            self.assertEqual(symbol, "ETHUSDT")
            self.assertEqual(interval, "1m")
            self.assertEqual(start_time_ms, 1777000000000)
            self.assertEqual(end_time_ms, 1777000059999)
            self.assertEqual(limit, 1500)
            return rows

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                result = sync_binance_futures_klines_range(
                    connection,
                    symbol="ethusdt",
                    interval="1m",
                    start_time_ms=1777000000000,
                    end_time_ms=1777000059999,
                    limit=1500,
                    fetcher=fake_fetcher,
                )

                instrument = InstrumentRepository(connection).get_by_market_symbol(
                    "CRYPTO_FUTURES", "ETHUSDT"
                )
                self.assertIsNotNone(instrument)
                assert instrument is not None
                bars = IntradayBarRepository(connection).list_for_instrument(
                    instrument.instrument_id,
                    "1m",
                )

        self.assertEqual(result.symbol, "ETHUSDT")
        self.assertEqual(result.interval, "1m")
        self.assertEqual(result.bars, 1)
        self.assertEqual(result.latest_close, 2307.25)
        self.assertEqual(instrument.market, "CRYPTO_FUTURES")
        self.assertEqual(instrument.instrument_type, "crypto_futures")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].source, "binance_futures_gap_fill")

    def test_select_top_futures_usdt_symbols_filters_perpetual_trading_usdt(self):
        symbols = select_top_futures_usdt_symbols(
            [
                {"symbol": "OLDUSDT", "quoteVolume": "9999"},
                {"symbol": "BTCUSDT", "quoteVolume": "1000"},
                {"symbol": "ETHUSDT", "quoteVolume": "2000"},
                {"symbol": "COINUSDT", "quoteVolume": "3000"},
                {"symbol": "BTCUSD_PERP", "quoteVolume": "999999"},
            ],
            {
                "OLDUSDT": {"contractType": "PERPETUAL", "status": "BREAK", "quoteAsset": "USDT"},
                "BTCUSDT": {"contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USDT"},
                "ETHUSDT": {"contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USDT"},
                "COINUSDT": {
                    "contractType": "TRADIFI_PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                },
                "BTCUSD_PERP": {"contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USD"},
            },
            limit=2,
        )

        self.assertEqual(symbols, ["COINUSDT", "ETHUSDT"])

    def test_select_futures_tradefi_symbols_uses_metadata_then_allowlist(self):
        symbols = select_futures_tradefi_symbols(
            [
                {"symbol": "COINUSDT", "quoteVolume": "100"},
                {"symbol": "BTCUSDT", "quoteVolume": "100000"},
                {"symbol": "MSTRUSDT", "quoteVolume": "50"},
            ],
            {
                "COINUSDT": {
                    "contractType": "TRADIFI_PERPETUAL",
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
