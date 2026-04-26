from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.binance import (
    binance_symbol_to_instrument,
    parse_binance_daily_kline,
    parse_binance_24hr_ticker_snapshot,
    parse_binance_symbol_trading_meta,
    parse_binance_kline,
    select_top_quote_volume_symbols,
    sync_binance_24hr_snapshots,
    sync_binance_daily_bars,
    sync_binance_klines,
)
from market.db import connect, init_database
from market.models import IntradayBar
from market.repositories import DailyBarRepository, InstrumentRepository, IntradayBarRepository


class BinanceTests(unittest.TestCase):
    def test_parse_binance_daily_kline_uses_quote_volume_as_turnover(self):
        bar = parse_binance_daily_kline(
            instrument_id=7,
            row=[
                1_775_952_000_000,
                "64000.10",
                "64200.20",
                "63950.30",
                "64100.40",
                "12.5",
                1_776_038_399_999,
                "801255.00",
            ],
            quote_currency="USDT",
            timezone_name="UTC",
        )

        self.assertEqual(bar.instrument_id, 7)
        self.assertEqual(bar.trade_date, "2026-04-12")
        self.assertEqual(bar.open, 64000.10)
        self.assertEqual(bar.high, 64200.20)
        self.assertEqual(bar.low, 63950.30)
        self.assertEqual(bar.close, 64100.40)
        self.assertEqual(bar.volume_raw, 12.5)
        self.assertEqual(bar.turnover_raw, 801255.0)
        self.assertEqual(bar.quote_currency, "USDT")
        self.assertEqual(bar.source, "binance")

    def test_parse_binance_24hr_ticker_snapshot_uses_24h_volume_turnover_and_change(self):
        instrument = binance_symbol_to_instrument("BTCUSDT")
        snapshot = parse_binance_24hr_ticker_snapshot(
            instrument_id=7,
            instrument=instrument,
            ticker={
                "symbol": "BTCUSDT",
                "lastPrice": "78012.00",
                "priceChangePercent": "-1.25",
                "volume": "12345.67",
                "quoteVolume": "987654321.12",
            },
            snapshot_ts_utc="2026-04-24T20:00:00Z",
            trade_date_local="2026-04-24",
        )

        self.assertEqual(snapshot.instrument_id, 7)
        self.assertEqual(snapshot.snapshot_ts_utc, "2026-04-24T20:00:00Z")
        self.assertEqual(snapshot.trade_date_local, "2026-04-24")
        self.assertEqual(snapshot.last_price, 78012.0)
        self.assertEqual(snapshot.change_pct, -1.25)
        self.assertEqual(snapshot.volume_raw, 12345.67)
        self.assertEqual(snapshot.turnover_raw, 987654321.12)
        self.assertEqual(snapshot.quote_currency, "USDT")
        self.assertEqual(snapshot.source, "binance_24hr")

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

    def test_parse_binance_symbol_trading_meta_extracts_original_tick_size(self):
        meta = parse_binance_symbol_trading_meta(
            {
                "symbols": [
                    {
                        "symbol": "DOGEUSDT",
                        "filters": [
                            {
                                "filterType": "PRICE_FILTER",
                                "tickSize": "0.00001000",
                            },
                            {
                                "filterType": "LOT_SIZE",
                                "stepSize": "1.00000000",
                            },
                        ],
                    }
                ]
            }
        )

        self.assertEqual(meta["price_tick_size"], "0.00001000")
        self.assertEqual(meta["quantity_step_size"], "1.00000000")

    def test_select_top_quote_volume_symbols_filters_usdt_and_sorts_descending(self):
        symbols = select_top_quote_volume_symbols(
            [
                {"symbol": "LOWUSDT", "quoteVolume": "10"},
                {"symbol": "BTCUSDT", "quoteVolume": "1000"},
                {"symbol": "ETHBTC", "quoteVolume": "999999"},
                {"symbol": "ETHUSDT", "quoteVolume": "500"},
            ],
            quote_asset="USDT",
            limit=2,
        )

        self.assertEqual(symbols, ["BTCUSDT", "ETHUSDT"])

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

    def test_sync_binance_24hr_snapshots_overwrites_kline_snapshot_for_board_semantics(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                sync_binance_klines(
                    connection,
                    symbol="BTCUSDT",
                    interval="15m",
                    limit=2,
                    now_ms=1_776_001_900_000,
                    fetcher=lambda _symbol, _interval, _limit: [
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
                    ],
                )
                count = sync_binance_24hr_snapshots(
                    connection,
                    tickers=[
                        {
                            "symbol": "BTCUSDT",
                            "lastPrice": "78012.00",
                            "priceChangePercent": "-1.25",
                            "volume": "12345.67",
                            "quoteVolume": "987654321.12",
                        }
                    ],
                    symbols=["BTCUSDT"],
                    snapshot_ts_utc="2026-04-12T20:00:00Z",
                    trade_date_local="2026-04-12",
                )
                row = connection.execute(
                    """
                    SELECT last_price, change_pct, volume_raw, turnover_raw, source
                    FROM market_snapshot
                    JOIN instrument
                        ON instrument.instrument_id = market_snapshot.instrument_id
                    WHERE instrument.market = 'CRYPTO'
                        AND instrument.symbol = 'BTCUSDT'
                        AND market_snapshot.trade_date_local = '2026-04-12'
                    """
                ).fetchone()

        self.assertEqual(count, 1)
        self.assertEqual(tuple(row), (78012.0, -1.25, 12345.67, 987654321.12, "binance_24hr"))

    def test_sync_binance_daily_bars_upserts_365_day_history(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            def fetcher(symbol: str, interval: str, limit: int) -> list[list[object]]:
                self.assertEqual(symbol, "BTCUSDT")
                self.assertEqual(interval, "1d")
                self.assertEqual(limit, 365)
                return [
                    [
                        1_775_952_000_000,
                        "64000",
                        "64200",
                        "63900",
                        "64100",
                        "10",
                        1_776_038_399_999,
                        "641000",
                    ],
                    [
                        1_776_038_400_000,
                        "64100",
                        "64300",
                        "64000",
                        "64250",
                        "12",
                        1_776_124_799_999,
                        "771000",
                    ],
                ]

            with connect(db_path) as connection:
                result = sync_binance_daily_bars(
                    connection,
                    symbol="BTCUSDT",
                    days=365,
                    fetcher=fetcher,
                )
                instrument = InstrumentRepository(connection).get_by_market_symbol(
                    "CRYPTO", "BTCUSDT"
                )
                assert instrument is not None
                bars = DailyBarRepository(connection).list_for_instrument(
                    instrument.instrument_id or 0
                )

        self.assertEqual(result.bars, 2)
        self.assertEqual(result.symbol, "BTCUSDT")
        self.assertEqual(result.interval, "1d")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].trade_date, "2026-04-12")
        self.assertEqual(bars[-1].close, 64250.0)
        self.assertEqual(bars[-1].turnover_raw, 771000.0)


if __name__ == "__main__":
    unittest.main()
