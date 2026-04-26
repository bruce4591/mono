from __future__ import annotations

import json
import sqlite3

from market.models import (
    AlertEvent,
    AlertRule,
    DailyBar,
    Instrument,
    IntradayBar,
    MarketSnapshot,
    RankingEntry,
    WatchlistEntry,
)


class InstrumentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert(self, instrument: Instrument) -> int:
        extra_meta = self._merged_extra_meta(instrument)
        self.connection.execute(
            """
            INSERT INTO instrument (
                market,
                symbol,
                display_name,
                exchange,
                instrument_type,
                quote_currency,
                timezone,
                is_active,
                extra_meta,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(market, symbol) DO UPDATE SET
                display_name = excluded.display_name,
                exchange = excluded.exchange,
                instrument_type = excluded.instrument_type,
                quote_currency = excluded.quote_currency,
                timezone = excluded.timezone,
                is_active = excluded.is_active,
                extra_meta = excluded.extra_meta,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                instrument.market,
                instrument.symbol,
                instrument.display_name,
                instrument.exchange,
                instrument.instrument_type,
                instrument.quote_currency,
                instrument.timezone,
                int(instrument.is_active),
                json.dumps(extra_meta, sort_keys=True),
            ),
        )
        row = self.connection.execute(
            """
            SELECT instrument_id
            FROM instrument
            WHERE market = ? AND symbol = ?
            """,
            (instrument.market, instrument.symbol),
        ).fetchone()
        if row is None:
            raise RuntimeError("instrument upsert did not return a row")
        return int(row["instrument_id"])

    def _merged_extra_meta(self, instrument: Instrument) -> dict[str, object]:
        existing = self.get_by_market_symbol(instrument.market, instrument.symbol)
        if existing is None:
            return dict(instrument.extra_meta)
        return {**existing.extra_meta, **instrument.extra_meta}

    def get_by_market_symbol(self, market: str, symbol: str) -> Instrument | None:
        row = self.connection.execute(
            """
            SELECT
                instrument_id,
                market,
                symbol,
                display_name,
                exchange,
                instrument_type,
                quote_currency,
                timezone,
                is_active,
                extra_meta
            FROM instrument
            WHERE market = ? AND symbol = ?
            """,
            (market, symbol),
        ).fetchone()
        if row is None:
            return None
        return Instrument(
            instrument_id=int(row["instrument_id"]),
            market=str(row["market"]),
            symbol=str(row["symbol"]),
            display_name=str(row["display_name"]),
            exchange=str(row["exchange"]),
            instrument_type=str(row["instrument_type"]),
            quote_currency=str(row["quote_currency"]),
            timezone=str(row["timezone"]),
            is_active=bool(row["is_active"]),
            extra_meta=json.loads(str(row["extra_meta"])),
        )


class DailyBarRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert(self, bar: DailyBar) -> None:
        self.connection.execute(
            """
            INSERT INTO bar_daily (
                instrument_id,
                trade_date,
                open,
                high,
                low,
                close,
                volume_raw,
                turnover_raw,
                quote_currency,
                source,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(instrument_id, trade_date) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume_raw = excluded.volume_raw,
                turnover_raw = excluded.turnover_raw,
                quote_currency = excluded.quote_currency,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                bar.instrument_id,
                bar.trade_date,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume_raw,
                bar.turnover_raw,
                bar.quote_currency,
                bar.source,
            ),
        )

    def list_for_instrument(self, instrument_id: int) -> list[DailyBar]:
        rows = self.connection.execute(
            """
            SELECT
                instrument_id,
                trade_date,
                open,
                high,
                low,
                close,
                volume_raw,
                turnover_raw,
                quote_currency,
                source
            FROM bar_daily
            WHERE instrument_id = ?
            ORDER BY trade_date
            """,
            (instrument_id,),
        ).fetchall()
        return [
            DailyBar(
                instrument_id=int(row["instrument_id"]),
                trade_date=str(row["trade_date"]),
                open=_optional_float(row["open"]),
                high=_optional_float(row["high"]),
                low=_optional_float(row["low"]),
                close=_optional_float(row["close"]),
                volume_raw=_optional_float(row["volume_raw"]),
                turnover_raw=_optional_float(row["turnover_raw"]),
                quote_currency=str(row["quote_currency"]),
                source=str(row["source"]),
            )
            for row in rows
        ]


class IntradayBarRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert(self, bar: IntradayBar) -> None:
        self.connection.execute(
            """
            INSERT INTO bar_intraday (
                instrument_id,
                interval,
                bar_start_ts_utc,
                bar_end_ts_utc,
                trade_date_local,
                open,
                high,
                low,
                close,
                volume_raw,
                turnover_raw,
                is_closed_bar,
                source,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(instrument_id, interval, bar_start_ts_utc) DO UPDATE SET
                bar_end_ts_utc = excluded.bar_end_ts_utc,
                trade_date_local = excluded.trade_date_local,
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume_raw = excluded.volume_raw,
                turnover_raw = excluded.turnover_raw,
                is_closed_bar = excluded.is_closed_bar,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                bar.instrument_id,
                bar.interval,
                bar.bar_start_ts_utc,
                bar.bar_end_ts_utc,
                bar.trade_date_local,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume_raw,
                bar.turnover_raw,
                int(bar.is_closed_bar),
                bar.source,
            ),
        )

    def list_for_instrument(self, instrument_id: int, interval: str) -> list[IntradayBar]:
        rows = self.connection.execute(
            """
            SELECT
                instrument_id,
                interval,
                bar_start_ts_utc,
                bar_end_ts_utc,
                trade_date_local,
                open,
                high,
                low,
                close,
                volume_raw,
                turnover_raw,
                is_closed_bar,
                source
            FROM bar_intraday
            WHERE instrument_id = ? AND interval = ?
            ORDER BY bar_start_ts_utc
            """,
            (instrument_id, interval),
        ).fetchall()
        return [
            IntradayBar(
                instrument_id=int(row["instrument_id"]),
                interval=str(row["interval"]),
                bar_start_ts_utc=str(row["bar_start_ts_utc"]),
                bar_end_ts_utc=str(row["bar_end_ts_utc"]),
                trade_date_local=str(row["trade_date_local"]),
                open=_optional_float(row["open"]),
                high=_optional_float(row["high"]),
                low=_optional_float(row["low"]),
                close=_optional_float(row["close"]),
                volume_raw=_optional_float(row["volume_raw"]),
                turnover_raw=_optional_float(row["turnover_raw"]),
                is_closed_bar=bool(row["is_closed_bar"]),
                source=str(row["source"]),
            )
            for row in rows
        ]


class WatchlistRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def replace(self, watchlist_name: str, entries: list[WatchlistEntry]) -> None:
        self.connection.execute(
            """
            UPDATE watchlist
            SET is_active = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE watchlist_name = ?
            """,
            (watchlist_name,),
        )
        for entry in entries:
            self.connection.execute(
                """
                INSERT INTO watchlist (
                    watchlist_name,
                    instrument_id,
                    sort_order,
                    is_active,
                    updated_at
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(watchlist_name, instrument_id) DO UPDATE SET
                    sort_order = excluded.sort_order,
                    is_active = excluded.is_active,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    watchlist_name,
                    entry.instrument_id,
                    entry.sort_order,
                    int(entry.is_active),
                ),
            )

    def list_active(self, watchlist_name: str) -> list[WatchlistEntry]:
        rows = self.connection.execute(
            """
            SELECT instrument_id, sort_order, is_active
            FROM watchlist
            WHERE watchlist_name = ? AND is_active = 1
            ORDER BY sort_order, instrument_id
            """,
            (watchlist_name,),
        ).fetchall()
        return [
            WatchlistEntry(
                instrument_id=int(row["instrument_id"]),
                sort_order=int(row["sort_order"]),
                is_active=bool(row["is_active"]),
            )
            for row in rows
        ]


class MarketSnapshotRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert(self, snapshot: MarketSnapshot) -> None:
        self.connection.execute(
            """
            INSERT INTO market_snapshot (
                instrument_id,
                snapshot_ts_utc,
                trade_date_local,
                last_price,
                change_pct,
                volume_raw,
                turnover_raw,
                quote_currency,
                source,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(instrument_id, trade_date_local) DO UPDATE SET
                snapshot_ts_utc = excluded.snapshot_ts_utc,
                last_price = excluded.last_price,
                change_pct = excluded.change_pct,
                volume_raw = excluded.volume_raw,
                turnover_raw = excluded.turnover_raw,
                quote_currency = excluded.quote_currency,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                snapshot.instrument_id,
                snapshot.snapshot_ts_utc,
                snapshot.trade_date_local,
                snapshot.last_price,
                snapshot.change_pct,
                snapshot.volume_raw,
                snapshot.turnover_raw,
                snapshot.quote_currency,
                snapshot.source,
            ),
        )


class RankingRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def refresh_turnover_board(
        self,
        *,
        board_name: str,
        snapshot_ts_utc: str,
        trade_date_local: str,
        market: str,
        instrument_type: str,
        limit: int,
        watchlist_name: str | None = None,
        source: str = "ranking_engine",
    ) -> int:
        self.connection.execute(
            """
            DELETE FROM ranking_snapshot
            WHERE board_name = ? AND snapshot_ts_utc = ?
            """,
            (board_name, snapshot_ts_utc),
        )

        rows = self._select_turnover_rows(
            trade_date_local=trade_date_local,
            market=market,
            instrument_type=instrument_type,
            limit=limit,
            watchlist_name=watchlist_name,
        )
        for index, row in enumerate(rows, start=1):
            self.connection.execute(
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    board_name,
                    snapshot_ts_utc,
                    index,
                    int(row["instrument_id"]),
                    float(row["turnover_raw"]),
                    str(row["quote_currency"]),
                    _optional_float(row["change_pct"]),
                    source,
                ),
            )
        return len(rows)

    def list_board(self, board_name: str, snapshot_ts_utc: str) -> list[RankingEntry]:
        rows = self.connection.execute(
            """
            SELECT
                board_name,
                snapshot_ts_utc,
                rank,
                instrument_id,
                turnover_raw,
                quote_currency,
                change_pct,
                source
            FROM ranking_snapshot
            WHERE board_name = ? AND snapshot_ts_utc = ?
            ORDER BY rank
            """,
            (board_name, snapshot_ts_utc),
        ).fetchall()
        return [
            RankingEntry(
                board_name=str(row["board_name"]),
                snapshot_ts_utc=str(row["snapshot_ts_utc"]),
                rank=int(row["rank"]),
                instrument_id=int(row["instrument_id"]),
                turnover_raw=float(row["turnover_raw"]),
                quote_currency=str(row["quote_currency"]),
                change_pct=_optional_float(row["change_pct"]),
                source=str(row["source"]),
            )
            for row in rows
        ]

    def _select_turnover_rows(
        self,
        *,
        trade_date_local: str,
        market: str,
        instrument_type: str,
        limit: int,
        watchlist_name: str | None,
    ) -> list[sqlite3.Row]:
        if watchlist_name is None:
            return self.connection.execute(
                """
                SELECT
                    market_snapshot.instrument_id,
                    market_snapshot.turnover_raw,
                    market_snapshot.quote_currency,
                    market_snapshot.change_pct
                FROM market_snapshot
                JOIN instrument
                    ON instrument.instrument_id = market_snapshot.instrument_id
                WHERE market_snapshot.trade_date_local = ?
                    AND instrument.market = ?
                    AND instrument.instrument_type = ?
                    AND instrument.is_active = 1
                    AND market_snapshot.turnover_raw IS NOT NULL
                ORDER BY market_snapshot.turnover_raw DESC, instrument.symbol
                LIMIT ?
                """,
                (trade_date_local, market, instrument_type, limit),
            ).fetchall()

        return self.connection.execute(
            """
            SELECT
                market_snapshot.instrument_id,
                market_snapshot.turnover_raw,
                market_snapshot.quote_currency,
                market_snapshot.change_pct
            FROM market_snapshot
            JOIN instrument
                ON instrument.instrument_id = market_snapshot.instrument_id
            JOIN watchlist
                ON watchlist.instrument_id = market_snapshot.instrument_id
                AND watchlist.watchlist_name = ?
                AND watchlist.is_active = 1
            WHERE market_snapshot.trade_date_local = ?
                AND instrument.market = ?
                AND instrument.instrument_type = ?
                AND instrument.is_active = 1
                AND market_snapshot.turnover_raw IS NOT NULL
            ORDER BY market_snapshot.turnover_raw DESC, instrument.symbol
            LIMIT ?
            """,
            (watchlist_name, trade_date_local, market, instrument_type, limit),
        ).fetchall()


class AlertRuleRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert(self, rule: AlertRule) -> int:
        self.connection.execute(
            """
            INSERT INTO alert_rule (
                name,
                market,
                symbol,
                metric,
                operator,
                threshold,
                is_active,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(name) DO UPDATE SET
                market = excluded.market,
                symbol = excluded.symbol,
                metric = excluded.metric,
                operator = excluded.operator,
                threshold = excluded.threshold,
                is_active = excluded.is_active,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                rule.name,
                rule.market,
                rule.symbol,
                rule.metric,
                rule.operator,
                rule.threshold,
                int(rule.is_active),
            ),
        )
        row = self.connection.execute(
            "SELECT rule_id FROM alert_rule WHERE name = ?",
            (rule.name,),
        ).fetchone()
        if row is None:
            raise RuntimeError("alert rule upsert did not return a row")
        return int(row["rule_id"])

    def list_active(self) -> list[AlertRule]:
        rows = self.connection.execute(
            """
            SELECT
                rule_id,
                name,
                market,
                symbol,
                metric,
                operator,
                threshold,
                is_active
            FROM alert_rule
            WHERE is_active = 1
            ORDER BY name
            """
        ).fetchall()
        return [_alert_rule_from_row(row) for row in rows]

    def list_all(self) -> list[AlertRule]:
        rows = self.connection.execute(
            """
            SELECT
                rule_id,
                name,
                market,
                symbol,
                metric,
                operator,
                threshold,
                is_active
            FROM alert_rule
            ORDER BY name
            """
        ).fetchall()
        return [_alert_rule_from_row(row) for row in rows]


class AlertEventRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def insert(self, event: AlertEvent) -> int:
        self.connection.execute(
            """
            INSERT INTO alert_event (
                rule_id,
                instrument_id,
                triggered_at_utc,
                metric,
                observed_value,
                threshold,
                message,
                is_acknowledged
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.rule_id,
                event.instrument_id,
                event.triggered_at_utc,
                event.metric,
                event.observed_value,
                event.threshold,
                event.message,
                int(event.is_acknowledged),
            ),
        )
        row = self.connection.execute("SELECT last_insert_rowid()").fetchone()
        if row is None:
            raise RuntimeError("alert event insert did not return a row")
        return int(row[0])

    def list_recent(self, limit: int = 50) -> list[AlertEvent]:
        rows = self.connection.execute(
            """
            SELECT
                alert_event.event_id,
                alert_event.rule_id,
                alert_rule.name AS rule_name,
                alert_event.instrument_id,
                instrument.market,
                instrument.symbol,
                alert_event.triggered_at_utc,
                alert_event.metric,
                alert_event.observed_value,
                alert_event.threshold,
                alert_event.message,
                alert_event.is_acknowledged
            FROM alert_event
            JOIN alert_rule
                ON alert_rule.rule_id = alert_event.rule_id
            JOIN instrument
                ON instrument.instrument_id = alert_event.instrument_id
            ORDER BY alert_event.triggered_at_utc DESC, alert_event.event_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            AlertEvent(
                event_id=int(row["event_id"]),
                rule_id=int(row["rule_id"]),
                rule_name=str(row["rule_name"]),
                instrument_id=int(row["instrument_id"]),
                market=str(row["market"]),
                symbol=str(row["symbol"]),
                triggered_at_utc=str(row["triggered_at_utc"]),
                metric=str(row["metric"]),
                observed_value=float(row["observed_value"]),
                threshold=float(row["threshold"]),
                message=str(row["message"]),
                is_acknowledged=bool(row["is_acknowledged"]),
            )
            for row in rows
        ]


def _alert_rule_from_row(row: sqlite3.Row) -> AlertRule:
    return AlertRule(
        rule_id=int(row["rule_id"]),
        name=str(row["name"]),
        market=str(row["market"]),
        symbol=str(row["symbol"]),
        metric=str(row["metric"]),
        operator=str(row["operator"]),
        threshold=float(row["threshold"]),
        is_active=bool(row["is_active"]),
    )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)
