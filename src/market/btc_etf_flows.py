from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.request import Request, urlopen

from market.models import DailyBar, Instrument, MarketSnapshot
from market.repositories import (
    DailyBarRepository,
    InstrumentRepository,
    MarketSnapshotRepository,
)


FARSIDE_BTC_ETF_FLOW_URL = "https://farside.co.uk/btc/"
BTC_ETF_FLOW_MARKET = "BTC_ETF_FLOW"
BTC_ETF_FLOW_BOARD = "BTC_ETF_FLOW_TOP5"
FARSIDE_BTC_ETF_FUNDS = {
    "IBIT",
    "FBTC",
    "BITB",
    "ARKB",
    "BTCO",
    "EZBC",
    "BRRR",
    "HODL",
    "BTCW",
    "MSBT",
    "GBTC",
    "BTC",
    "TOTAL",
}


@dataclass(frozen=True)
class BtcEtfFlowRow:
    flow_date: str
    fund_symbol: str
    fund_name: str
    net_flow_usd_m: float
    btc_price_usd: float | None
    estimated_btc: float | None
    source: str
    source_url: str


@dataclass(frozen=True)
class BtcEtfFlowSyncResult:
    rows_synced: int
    bars_synced: int
    rankings_synced: int
    source: str
    source_url: str


class _HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_cell = False
        self._current_cell: list[str] = []
        self._current_row: list[str] | None = None
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._current_row = []
        if tag in {"td", "th"} and self._current_row is not None:
            self._in_cell = True
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._current_row is not None:
            text = " ".join("".join(self._current_cell).split())
            self._current_row.append(text)
            self._in_cell = False
            self._current_cell = []
        if tag == "tr" and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None


def fetch_farside_btc_etf_flow_html(source_url: str = FARSIDE_BTC_ETF_FLOW_URL) -> str:
    request = Request(
        source_url,
        headers={
            "User-Agent": "market-mvp/0.1 (+https://github.com/bruce4591/mono)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def sync_farside_btc_etf_flows(
    connection: sqlite3.Connection,
    *,
    html: str | None = None,
    source_url: str = FARSIDE_BTC_ETF_FLOW_URL,
    fund_symbol: str | None = None,
    limit_days: int | None = 30,
) -> BtcEtfFlowSyncResult:
    ensure_btc_etf_flow_table(connection)
    payload = html if html is not None else fetch_farside_btc_etf_flow_html(source_url)
    rows = parse_farside_btc_etf_flows(payload, source_url=source_url)
    if not rows:
        raise RuntimeError(
            "no BTC ETF flow rows parsed from Farside; source may be blocked or layout changed"
        )
    if fund_symbol is not None:
        normalized_symbol = fund_symbol.upper()
        rows = [row for row in rows if row.fund_symbol == normalized_symbol]
    if limit_days is not None:
        dates = sorted({row.flow_date for row in rows}, reverse=True)[:limit_days]
        allowed_dates = set(dates)
        rows = [row for row in rows if row.flow_date in allowed_dates]
    synced = 0
    enriched_rows: list[BtcEtfFlowRow] = []
    for row in rows:
        btc_price = _btc_price_for_date(connection, row.flow_date)
        enriched = BtcEtfFlowRow(
            flow_date=row.flow_date,
            fund_symbol=row.fund_symbol,
            fund_name=row.fund_name,
            net_flow_usd_m=row.net_flow_usd_m,
            btc_price_usd=btc_price,
            estimated_btc=(
                (row.net_flow_usd_m * 1_000_000.0) / btc_price
                if btc_price is not None and btc_price > 0
                else None
            ),
            source=row.source,
            source_url=row.source_url,
        )
        _upsert_btc_etf_flow(connection, enriched)
        enriched_rows.append(enriched)
        synced += 1
    bars_synced = _upsert_btc_etf_flow_bars(connection, enriched_rows)
    rankings_synced = _refresh_btc_etf_flow_rankings(connection, enriched_rows)
    return BtcEtfFlowSyncResult(
        rows_synced=synced,
        bars_synced=bars_synced,
        rankings_synced=rankings_synced,
        source="farside",
        source_url=source_url,
    )


def parse_farside_btc_etf_flows(
    html: str,
    *,
    source_url: str = FARSIDE_BTC_ETF_FLOW_URL,
) -> list[BtcEtfFlowRow]:
    parser = _HtmlTableParser()
    parser.feed(html)
    header: list[str] | None = None
    previous_header: list[str] | None = None
    rows: list[BtcEtfFlowRow] = []
    for raw_cells in parser.rows:
        cells = [cell.strip() for cell in raw_cells if cell.strip()]
        if not cells:
            continue
        upper_cells = [cell.upper() for cell in cells]
        if "FBTC" in upper_cells and "IBIT" in upper_cells:
            header = [
                cell or (previous_header[index] if previous_header and index < len(previous_header) else "")
                for index, cell in enumerate(raw_cells)
            ]
            header = [cell.strip() for cell in header if cell.strip()]
            if header[0].upper() != "DATE":
                header = ["Date", *header]
            continue
        if any(cell.upper() == "TOTAL" for cell in cells):
            previous_header = [cell.strip() for cell in raw_cells]
        if header is None or len(cells) < 2:
            continue
        parsed_date = _parse_farside_date(cells[0])
        if parsed_date is None:
            continue
        if len(cells) == len(header) - 1:
            values = [cells[0], *cells[1:]]
        else:
            values = cells[: len(header)]
        for column, value in zip(header[1:], values[1:], strict=False):
            fund_symbol = _normalize_fund_symbol(column)
            if fund_symbol == "TOTALUS$M":
                fund_symbol = "TOTAL"
            if fund_symbol not in FARSIDE_BTC_ETF_FUNDS:
                continue
            net_flow_usd_m = _parse_farside_number(value)
            if net_flow_usd_m is None:
                continue
            rows.append(
                BtcEtfFlowRow(
                    flow_date=parsed_date,
                    fund_symbol=fund_symbol,
                    fund_name=fund_symbol,
                    net_flow_usd_m=net_flow_usd_m,
                    btc_price_usd=None,
                    estimated_btc=None,
                    source="farside",
                    source_url=source_url,
                )
            )
    return rows


def ensure_btc_etf_flow_table(connection: sqlite3.Connection) -> None:
    if getattr(connection, "backend", "sqlite") == "postgres":
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS btc_etf_flow (
                flow_date DATE NOT NULL,
                fund_symbol TEXT NOT NULL,
                fund_name TEXT NOT NULL,
                net_flow_usd_m DOUBLE PRECISION NOT NULL,
                btc_price_usd DOUBLE PRECISION,
                estimated_btc DOUBLE PRECISION,
                source TEXT NOT NULL,
                source_url TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (flow_date, fund_symbol, source)
            )
            """
        )
        return
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS btc_etf_flow (
            flow_date TEXT NOT NULL,
            fund_symbol TEXT NOT NULL,
            fund_name TEXT NOT NULL,
            net_flow_usd_m REAL NOT NULL,
            btc_price_usd REAL,
            estimated_btc REAL,
            source TEXT NOT NULL,
            source_url TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (flow_date, fund_symbol, source)
        )
        """
    )


def _upsert_btc_etf_flow(
    connection: sqlite3.Connection,
    row: BtcEtfFlowRow,
) -> None:
    connection.execute(
        """
        INSERT INTO btc_etf_flow (
            flow_date,
            fund_symbol,
            fund_name,
            net_flow_usd_m,
            btc_price_usd,
            estimated_btc,
            source,
            source_url,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(flow_date, fund_symbol, source) DO UPDATE SET
            fund_name = excluded.fund_name,
            net_flow_usd_m = excluded.net_flow_usd_m,
            btc_price_usd = excluded.btc_price_usd,
            estimated_btc = excluded.estimated_btc,
            source_url = excluded.source_url,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            row.flow_date,
            row.fund_symbol,
            row.fund_name,
            row.net_flow_usd_m,
            row.btc_price_usd,
            row.estimated_btc,
            row.source,
            row.source_url,
        ),
    )


def _upsert_btc_etf_flow_bars(
    connection: sqlite3.Connection,
    rows: list[BtcEtfFlowRow],
) -> int:
    instruments = InstrumentRepository(connection)
    bars = DailyBarRepository(connection)
    snapshots = MarketSnapshotRepository(connection)
    count = 0
    for row in rows:
        if row.estimated_btc is None:
            continue
        instrument_id = instruments.upsert(
            Instrument(
                market=BTC_ETF_FLOW_MARKET,
                symbol=row.fund_symbol,
                display_name=f"{row.fund_symbol} BTC Flow",
                exchange="FARSIDE",
                instrument_type="btc_etf_flow",
                quote_currency="BTC",
                timezone="UTC",
                extra_meta={
                    "source": row.source,
                    "source_url": row.source_url,
                    "net_flow_unit": "BTC",
                },
            )
        )
        flow_btc = row.estimated_btc
        bars.upsert(
            DailyBar(
                instrument_id=instrument_id,
                trade_date=row.flow_date,
                open=0.0,
                high=max(0.0, flow_btc),
                low=min(0.0, flow_btc),
                close=flow_btc,
                volume_raw=abs(flow_btc),
                turnover_raw=row.net_flow_usd_m * 1_000_000.0,
                quote_currency="BTC",
                source=row.source,
            )
        )
        snapshots.upsert(
            MarketSnapshot(
                instrument_id=instrument_id,
                snapshot_ts_utc=f"{row.flow_date}T23:59:59Z",
                trade_date_local=row.flow_date,
                last_price=flow_btc,
                change_pct=None,
                volume_raw=abs(flow_btc),
                turnover_raw=row.net_flow_usd_m * 1_000_000.0,
                quote_currency="BTC",
                source=row.source,
            )
        )
        count += 1
    return count


def _refresh_btc_etf_flow_rankings(
    connection: sqlite3.Connection,
    rows: list[BtcEtfFlowRow],
) -> int:
    instruments = InstrumentRepository(connection)
    rows_by_date: dict[str, list[BtcEtfFlowRow]] = {}
    for row in rows:
        if row.fund_symbol == "TOTAL" or row.estimated_btc is None:
            continue
        rows_by_date.setdefault(row.flow_date, []).append(row)
    total = 0
    for flow_date, date_rows in rows_by_date.items():
        snapshot_ts_utc = f"{flow_date}T23:59:59Z"
        connection.execute(
            """
            DELETE FROM ranking_snapshot
            WHERE board_name = ? AND snapshot_ts_utc = ?
            """,
            (BTC_ETF_FLOW_BOARD, snapshot_ts_utc),
        )
        ranked_rows = sorted(
            date_rows,
            key=lambda row: abs(row.estimated_btc or 0.0),
            reverse=True,
        )[:5]
        for rank, row in enumerate(ranked_rows, start=1):
            instrument_id = instruments.upsert(
                Instrument(
                    market=BTC_ETF_FLOW_MARKET,
                    symbol=row.fund_symbol,
                    display_name=f"{row.fund_symbol} BTC Flow",
                    exchange="FARSIDE",
                    instrument_type="btc_etf_flow",
                    quote_currency="BTC",
                    timezone="UTC",
                    extra_meta={"source": row.source, "source_url": row.source_url},
                )
            )
            connection.execute(
                """
                INSERT INTO ranking_snapshot (
                    board_name,
                    snapshot_ts_utc,
                    rank,
                    instrument_id,
                    turnover_raw,
                    quote_currency,
                    change_pct,
                    source,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'BTC', NULL, ?, CURRENT_TIMESTAMP)
                """,
                (
                    BTC_ETF_FLOW_BOARD,
                    snapshot_ts_utc,
                    rank,
                    instrument_id,
                    abs(row.estimated_btc or 0.0),
                    row.source,
                ),
            )
            total += 1
    return total


def _btc_price_for_date(connection: sqlite3.Connection, flow_date: str) -> float | None:
    row = connection.execute(
        """
        SELECT bar_daily.close
        FROM instrument
        JOIN bar_daily
            ON bar_daily.instrument_id = instrument.instrument_id
        WHERE instrument.symbol = 'BTCUSDT'
            AND instrument.market IN ('CRYPTO_FUTURES', 'CRYPTO')
            AND bar_daily.trade_date <= ?
            AND bar_daily.close IS NOT NULL
        ORDER BY bar_daily.trade_date DESC,
            CASE instrument.market WHEN 'CRYPTO_FUTURES' THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (flow_date,),
    ).fetchone()
    if row is None:
        return None
    return float(row["close"])


def _parse_farside_date(value: str) -> str | None:
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC).date().isoformat()
        except ValueError:
            continue
    return None


def _normalize_fund_symbol(value: str) -> str:
    return value.strip().upper().replace(" ", "")


def _parse_farside_number(value: str) -> float | None:
    cleaned = value.strip().replace(",", "")
    if not cleaned or cleaned in {"-", "—"}:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -number if negative else number
