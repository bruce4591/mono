from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from market.binance import binance_symbol_to_instrument
from market.binance_futures import binance_futures_symbol_to_instrument
from market.crypto_gaps import fill_binance_1m_gaps, fill_binance_futures_1m_gaps
from market.db import connect, init_database
from market.models import IntradayBar
from market.repositories import InstrumentRepository, IntradayBarRepository


class CryptoGapFillTests(unittest.TestCase):
    def test_fill_binance_1m_gaps_only_fetches_missing_minutes_and_aggregates(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def fetcher(
                symbol: str,
                interval: str,
                start_time_ms: int,
                end_time_ms: int,
                limit: int,
            ) -> list[list[object]]:
                calls.append((symbol, interval, start_time_ms, end_time_ms, limit))
                return [
                    [
                        _ms("2026-04-24T00:01:00Z"),
                        "101",
                        "102",
                        "100",
                        "101.5",
                        "2",
                        _ms("2026-04-24T00:01:59.999Z"),
                        "203",
                    ]
                ]

            with connect(db_path) as connection:
                instrument_id = InstrumentRepository(connection).upsert(
                    binance_symbol_to_instrument("BTCUSDT")
                )
                repository = IntradayBarRepository(connection)
                repository.upsert(
                    _bar(
                        instrument_id,
                        start="2026-04-24T00:00:00Z",
                        end="2026-04-24T00:01:00Z",
                        close=100.0,
                    )
                )
                repository.upsert(
                    _bar(
                        instrument_id,
                        start="2026-04-24T00:02:00Z",
                        end="2026-04-24T00:03:00Z",
                        close=103.0,
                    )
                )

                result = fill_binance_1m_gaps(
                    connection,
                    symbols=["BTCUSDT"],
                    start_ts_utc="2026-04-24T00:00:00Z",
                    end_ts_utc="2026-04-24T00:03:00Z",
                    fetcher=fetcher,
                    now_ms=_ms("2026-04-24T00:04:00Z"),
                )

                rows = connection.execute(
                    """
                    SELECT interval, bar_start_ts_utc, close, source
                    FROM bar_intraday
                    JOIN instrument
                        ON instrument.instrument_id = bar_intraday.instrument_id
                    WHERE instrument.symbol = 'BTCUSDT'
                    ORDER BY interval, bar_start_ts_utc
                    """
                ).fetchall()
                daily_count = connection.execute(
                    "SELECT count(*) FROM bar_daily WHERE source = 'aggregate_1m'"
                ).fetchone()[0]

        self.assertEqual(
            calls,
            [
                (
                    "BTCUSDT",
                    "1m",
                    _ms("2026-04-24T00:01:00Z"),
                    _ms("2026-04-24T00:01:59.999Z"),
                    1,
                )
            ],
        )
        self.assertEqual(result.symbols_checked, 1)
        self.assertEqual(result.gaps_filled, 1)
        self.assertEqual(result.bars_written, 1)
        self.assertIn(
            ("1m", "2026-04-24T00:01:00Z", 101.5, "binance_gap_fill"),
            [tuple(row) for row in rows],
        )
        self.assertIn(
            ("5m", "2026-04-24T00:00:00Z", 103.0, "aggregate_1m"),
            [tuple(row) for row in rows],
        )
        self.assertEqual(daily_count, 1)

    def test_fill_binance_1m_gaps_skips_rest_when_window_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                instrument_id = InstrumentRepository(connection).upsert(
                    binance_symbol_to_instrument("ETHUSDT")
                )
                repository = IntradayBarRepository(connection)
                repository.upsert(
                    _bar(
                        instrument_id,
                        start="2026-04-24T00:00:00Z",
                        end="2026-04-24T00:01:00Z",
                        close=3000.0,
                    )
                )
                repository.upsert(
                    _bar(
                        instrument_id,
                        start="2026-04-24T00:01:00Z",
                        end="2026-04-24T00:02:00Z",
                        close=3001.0,
                    )
                )

                result = fill_binance_1m_gaps(
                    connection,
                    symbols=["ETHUSDT"],
                    start_ts_utc="2026-04-24T00:00:00Z",
                    end_ts_utc="2026-04-24T00:02:00Z",
                    fetcher=_raise_fetcher,
                    now_ms=_ms("2026-04-24T00:03:00Z"),
                )

        self.assertEqual(result.gaps_filled, 0)
        self.assertEqual(result.bars_written, 0)

    def test_fill_binance_1m_gaps_refetches_open_partial_bars(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def fetcher(
                symbol: str,
                interval: str,
                start_time_ms: int,
                end_time_ms: int,
                limit: int,
            ) -> list[list[object]]:
                calls.append((symbol, interval, start_time_ms, end_time_ms, limit))
                return [
                    [
                        _ms("2026-05-02T14:51:00Z"),
                        "2306.74",
                        "2308.00",
                        "2306.73",
                        "2308.00",
                        "36.1682",
                        _ms("2026-05-02T14:51:59.999Z"),
                        "83449.000684",
                    ]
                ]

            with connect(db_path) as connection:
                instrument_id = InstrumentRepository(connection).upsert(
                    binance_symbol_to_instrument("ETHUSDT")
                )
                IntradayBarRepository(connection).upsert(
                    _bar(
                        instrument_id,
                        start="2026-05-02T14:51:00Z",
                        end="2026-05-02T14:52:00Z",
                        close=2306.94,
                        is_closed_bar=False,
                    )
                )

                result = fill_binance_1m_gaps(
                    connection,
                    symbols=["ETHUSDT"],
                    start_ts_utc="2026-05-02T14:51:00Z",
                    end_ts_utc="2026-05-02T14:52:00Z",
                    fetcher=fetcher,
                    now_ms=_ms("2026-05-02T14:53:00Z"),
                )

                row = connection.execute(
                    """
                    SELECT high, low, close, volume_raw, turnover_raw, is_closed_bar, source
                    FROM bar_intraday
                    JOIN instrument
                        ON instrument.instrument_id = bar_intraday.instrument_id
                    WHERE instrument.symbol = 'ETHUSDT'
                        AND interval = '1m'
                        AND bar_start_ts_utc = '2026-05-02T14:51:00Z'
                    """
                ).fetchone()

        self.assertEqual(
            calls,
            [
                (
                    "ETHUSDT",
                    "1m",
                    _ms("2026-05-02T14:51:00Z"),
                    _ms("2026-05-02T14:51:59.999Z"),
                    1,
                )
            ],
        )
        self.assertEqual(result.gaps_filled, 1)
        self.assertEqual(result.bars_written, 1)
        self.assertEqual(
            tuple(row),
            (2308.0, 2306.73, 2308.0, 36.1682, 83449.000684, 1, "binance_gap_fill"),
        )

    def test_fill_binance_futures_1m_gaps_writes_futures_market_only(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            calls = []

            def fetcher(
                symbol: str,
                interval: str,
                start_time_ms: int,
                end_time_ms: int,
                limit: int,
            ) -> list[list[object]]:
                calls.append((symbol, interval, start_time_ms, end_time_ms, limit))
                return [
                    [
                        _ms("2026-05-03T00:01:00Z"),
                        "10",
                        "12",
                        "9",
                        "11",
                        "2",
                        _ms("2026-05-03T00:01:59.999Z"),
                        "22",
                    ]
                ]

            with connect(db_path) as connection:
                futures_id = InstrumentRepository(connection).upsert(
                    binance_futures_symbol_to_instrument({"symbol": "ETHUSDT"})
                )
                IntradayBarRepository(connection).upsert(
                    _bar(
                        futures_id,
                        start="2026-05-03T00:00:00Z",
                        end="2026-05-03T00:01:00Z",
                        close=10.0,
                    )
                )

                result = fill_binance_futures_1m_gaps(
                    connection,
                    symbols=["ETHUSDT"],
                    start_ts_utc="2026-05-03T00:00:00Z",
                    end_ts_utc="2026-05-03T00:02:00Z",
                    fetcher=fetcher,
                    now_ms=_ms("2026-05-03T00:03:00Z"),
                )

                row = connection.execute(
                    """
                    SELECT instrument.market, bar_intraday.source, bar_intraday.close
                    FROM bar_intraday
                    JOIN instrument ON instrument.instrument_id = bar_intraday.instrument_id
                    WHERE instrument.symbol = 'ETHUSDT'
                        AND bar_intraday.bar_start_ts_utc = '2026-05-03T00:01:00Z'
                    """
                ).fetchone()

        self.assertEqual(calls[0][0], "ETHUSDT")
        self.assertEqual(result.gaps_filled, 1)
        self.assertEqual(tuple(row), ("CRYPTO_FUTURES", "binance_futures_gap_fill", 11.0))


def _bar(
    instrument_id: int,
    *,
    start: str,
    end: str,
    close: float,
    is_closed_bar: bool = True,
) -> IntradayBar:
    return IntradayBar(
        instrument_id=instrument_id,
        interval="1m",
        bar_start_ts_utc=start,
        bar_end_ts_utc=end,
        trade_date_local=start[:10],
        open=close,
        high=close,
        low=close,
        close=close,
        volume_raw=1.0,
        turnover_raw=close,
        is_closed_bar=is_closed_bar,
        source="binance_ws_kline",
    )


def _ms(value: str) -> int:
    normalized = value.replace("Z", "+00:00")
    return int(datetime.fromisoformat(normalized).astimezone(UTC).timestamp() * 1000)


def _raise_fetcher(
    _symbol: str,
    _interval: str,
    _start_time_ms: int,
    _end_time_ms: int,
    _limit: int,
) -> list[list[object]]:
    raise AssertionError("fetcher should not be called when no gaps exist")


if __name__ == "__main__":
    unittest.main()
