from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from market.collectors.akshare import (
    AKSHARE_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
    AkshareCollector,
    parse_akshare_daily_frame,
)
from market.db import connect, init_database
from market.watchlists import sync_watchlist_from_file


class FakeFrame:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = records

    def to_dict(self, orient: str) -> list[dict[str, object]]:
        if orient != "records":
            raise ValueError("unexpected orient")
        return self.records


class AkshareNormalizerTests(unittest.TestCase):
    def test_parse_akshare_daily_frame_normalizes_daily_rows(self):
        bars = parse_akshare_daily_frame(
            instrument_id=7,
            frame=FakeFrame(
                [
                    {
                        "date": "2026-04-23",
                        "open": "100.0",
                        "high": "105.0",
                        "low": "99.0",
                        "close": "104.0",
                        "volume": "1000",
                        "amount": "104000",
                    },
                    {
                        "date": "2026-04-24",
                        "open": "104.0",
                        "high": "108.0",
                        "low": "103.0",
                        "close": "106.0",
                        "volume": "1500",
                        "amount": "",
                    },
                ]
            ),
            quote_currency="USD",
            source="akshare",
        )

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].trade_date, "2026-04-23")
        self.assertEqual(bars[0].turnover_raw, 104000.0)
        self.assertEqual(bars[1].close, 106.0)
        self.assertEqual(bars[1].volume_raw, 1500.0)
        self.assertEqual(bars[1].turnover_raw, 159000.0)
        self.assertEqual(bars[1].quote_currency, "USD")
        self.assertEqual(bars[1].source, "akshare")

    def test_akshare_collector_syncs_focus_watchlists_with_rate_limit(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            etf_config = Path(tmp_dir) / "etf.json"
            index_config = Path(tmp_dir) / "index.json"
            etf_config.write_text(
                json.dumps(
                    {
                        "watchlist_name": "ETF_FOCUS20",
                        "entries": [
                            {
                                "market": "US",
                                "symbol": "SPY",
                                "display_name": "SPDR S&P 500 ETF Trust",
                                "exchange": "NYSEARCA",
                                "instrument_type": "etf",
                                "quote_currency": "USD",
                                "timezone": "America/New_York",
                                "sort_order": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            index_config.write_text(
                json.dumps(
                    {
                        "watchlist_name": "INDEX_FOCUS20",
                        "entries": [
                            {
                                "market": "US",
                                "symbol": "SPX",
                                "display_name": "S&P 500 Index",
                                "exchange": "CBOE",
                                "instrument_type": "index",
                                "quote_currency": "USD",
                                "timezone": "America/New_York",
                                "sort_order": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            init_database(db_path)
            calls = []
            sleeps = []

            def fetcher(instrument):
                calls.append((instrument.instrument_type, instrument.symbol))
                return FakeFrame(
                    [
                        {
                            "date": "2026-04-23",
                            "open": "100",
                            "high": "105",
                            "low": "99",
                            "close": "104",
                            "volume": "1000",
                            "amount": "104000",
                        },
                        {
                            "date": "2026-04-24",
                            "open": "104",
                            "high": "108",
                            "low": "103",
                            "close": "106",
                            "volume": "1500",
                            "amount": "159000",
                        },
                    ]
                )

            with connect(db_path) as connection:
                sync_watchlist_from_file(connection, etf_config)
                sync_watchlist_from_file(connection, index_config)
                collector = AkshareCollector(
                    daily_fetcher=fetcher,
                    min_request_interval_seconds=AKSHARE_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
                    sleep=sleeps.append,
                )
                result = collector.sync_focus(
                    connection,
                    watchlist_names=["ETF_FOCUS20", "INDEX_FOCUS20"],
                    days=2,
                    snapshot_ts_utc="2026-04-24T21:00:00Z",
                    trade_date_local="2026-04-24",
                )
                daily_count = connection.execute("SELECT count(*) FROM bar_daily").fetchone()[0]
                snapshots = connection.execute(
                    """
                    SELECT instrument.symbol, market_snapshot.last_price, market_snapshot.change_pct
                    FROM market_snapshot
                    JOIN instrument
                        ON instrument.instrument_id = market_snapshot.instrument_id
                    ORDER BY instrument.symbol
                    """
                ).fetchall()

        self.assertEqual(result.source_name, "akshare")
        self.assertEqual(result.items_synced, 4)
        self.assertEqual(result.metadata["watchlists"], ["ETF_FOCUS20", "INDEX_FOCUS20"])
        self.assertEqual(calls, [("etf", "SPY"), ("index", "SPX")])
        self.assertEqual(len(sleeps), 1)
        self.assertEqual(daily_count, 4)
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0]["last_price"], 106.0)
        self.assertAlmostEqual(snapshots[0]["change_pct"], ((106.0 - 104.0) / 104.0) * 100)


if __name__ == "__main__":
    unittest.main()
