from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    migration_id: str
    sql: str


SQLITE_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        migration_id="0001_initial_schema",
        sql="""
        INSERT OR IGNORE INTO schema_migration (migration_id)
        VALUES ('0001_initial_schema')
        """,
    ),
    Migration(
        migration_id="0002_split_market_snapshots",
        sql="""
        CREATE TABLE IF NOT EXISTS latest_market_snapshot (
            instrument_id INTEGER PRIMARY KEY,
            snapshot_ts_utc TEXT NOT NULL,
            trade_date_local TEXT NOT NULL,
            last_price REAL,
            change_pct REAL,
            volume_raw REAL,
            turnover_raw REAL,
            quote_currency TEXT NOT NULL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id)
        );

        CREATE TABLE IF NOT EXISTS market_snapshot_history (
            instrument_id INTEGER NOT NULL,
            snapshot_ts_utc TEXT NOT NULL,
            trade_date_local TEXT NOT NULL,
            last_price REAL,
            change_pct REAL,
            volume_raw REAL,
            turnover_raw REAL,
            quote_currency TEXT NOT NULL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (instrument_id, snapshot_ts_utc),
            FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id)
        );

        INSERT OR IGNORE INTO latest_market_snapshot (
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
        SELECT
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
        FROM market_snapshot;

        INSERT OR IGNORE INTO market_snapshot_history (
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
        SELECT
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
        FROM market_snapshot;

        CREATE INDEX IF NOT EXISTS idx_latest_market_snapshot_turnover
            ON latest_market_snapshot (trade_date_local, turnover_raw DESC);

        CREATE INDEX IF NOT EXISTS idx_market_snapshot_history_lookup
            ON market_snapshot_history (instrument_id, snapshot_ts_utc DESC);
        """,
    ),
    Migration(
        migration_id="0003_board_refresh_state",
        sql="""
        CREATE TABLE IF NOT EXISTS board_refresh_state (
            board_name TEXT PRIMARY KEY,
            last_requested_at_utc TEXT,
            last_started_at_utc TEXT,
            last_finished_at_utc TEXT,
            status TEXT NOT NULL DEFAULT 'idle',
            last_error TEXT,
            updated_at_utc TEXT NOT NULL
        );
        """,
    ),
)


def ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration (
            migration_id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def applied_migration_ids(connection: sqlite3.Connection) -> set[str]:
    ensure_migration_table(connection)
    rows = connection.execute("SELECT migration_id FROM schema_migration").fetchall()
    return {str(row["migration_id"]) for row in rows}


def apply_sqlite_migrations(
    connection: sqlite3.Connection,
    migrations: Iterable[Migration] = SQLITE_MIGRATIONS,
) -> None:
    ensure_migration_table(connection)
    applied = applied_migration_ids(connection)
    for migration in migrations:
        if migration.migration_id in applied:
            continue
        connection.executescript(migration.sql)
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_migration (migration_id)
            VALUES (?)
            """,
            (migration.migration_id,),
        )
