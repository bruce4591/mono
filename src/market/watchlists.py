from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from market.models import Instrument, WatchlistEntry
from market.repositories import InstrumentRepository, WatchlistRepository


def sync_watchlist_from_file(connection: sqlite3.Connection, path: Path | str) -> int:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    watchlist_name = str(payload["watchlist_name"])
    entries = [_entry_from_payload(item) for item in payload["entries"]]

    instruments = InstrumentRepository(connection)
    watchlist_entries: list[WatchlistEntry] = []
    for instrument, sort_order in entries:
        instrument_id = instruments.upsert(instrument)
        watchlist_entries.append(
            WatchlistEntry(instrument_id=instrument_id, sort_order=sort_order)
        )

    WatchlistRepository(connection).replace(watchlist_name, watchlist_entries)
    return len(watchlist_entries)


def _entry_from_payload(payload: dict[str, Any]) -> tuple[Instrument, int]:
    instrument = Instrument(
        market=str(payload["market"]),
        symbol=str(payload["symbol"]),
        display_name=str(payload["display_name"]),
        exchange=str(payload["exchange"]),
        instrument_type=str(payload["instrument_type"]),
        quote_currency=str(payload["quote_currency"]),
        timezone=str(payload["timezone"]),
        extra_meta=dict(payload.get("extra_meta", {})),
    )
    return instrument, int(payload["sort_order"])
