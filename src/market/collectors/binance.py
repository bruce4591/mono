from __future__ import annotations

import sqlite3

from market.binance import KlineFetcher, fetch_binance_klines, sync_binance_klines
from market.collectors.base import CollectorResult


class BinanceCollector:
    source_name = "binance"

    def __init__(
        self,
        *,
        fetcher: KlineFetcher = fetch_binance_klines,
        now_ms: int | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.now_ms = now_ms

    def sync_universe(self, connection: sqlite3.Connection) -> CollectorResult:
        return CollectorResult(source_name=self.source_name, items_synced=0)

    def sync_daily_bars(
        self,
        connection: sqlite3.Connection,
        symbols: list[str],
        days: int,
    ) -> CollectorResult:
        return CollectorResult(
            source_name=self.source_name,
            items_synced=0,
            metadata={"symbols": [symbol.upper() for symbol in symbols], "days": days},
        )

    def sync_intraday_bars(
        self,
        connection: sqlite3.Connection,
        symbols: list[str],
        interval: str,
        limit: int,
    ) -> CollectorResult:
        normalized_symbols = [symbol.upper() for symbol in symbols]
        results = [
            sync_binance_klines(
                connection,
                symbol=symbol,
                interval=interval,
                limit=limit,
                now_ms=self.now_ms,
                fetcher=self.fetcher,
            )
            for symbol in normalized_symbols
        ]
        return CollectorResult(
            source_name=self.source_name,
            items_synced=sum(result.bars for result in results),
            metadata={
                "symbols": normalized_symbols,
                "interval": interval,
                "limit": limit,
            },
        )

    def sync_snapshots(
        self,
        connection: sqlite3.Connection,
        symbols: list[str],
    ) -> CollectorResult:
        return CollectorResult(
            source_name=self.source_name,
            items_synced=0,
            metadata={"symbols": [symbol.upper() for symbol in symbols]},
        )
