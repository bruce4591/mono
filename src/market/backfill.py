from __future__ import annotations

import sqlite3
from collections.abc import Sequence


ONLINE_BACKFILL_TABLES: tuple[str, ...] = (
    "instrument",
    "bar_daily",
    "bar_intraday",
    "market_snapshot",
    "latest_market_snapshot",
    "market_snapshot_history",
    "ranking_snapshot",
    "watchlist",
    "job_state",
    "source_health",
    "alert_rule",
    "alert_event",
    "push_device",
    "mobile_alert_rule",
    "indicator_definition",
    "indicator_value",
    "mobile_alert_event",
    "mobile_alert_delivery",
    "device_checkpoint",
    "device_session",
    "board_refresh_state",
)


def list_table_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [str(row["name"]) for row in rows]


def fetch_sqlite_rows(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    columns: Sequence[str],
) -> list[dict[str, object]]:
    column_sql = ", ".join(columns)
    rows = connection.execute(f"SELECT {column_sql} FROM {table_name}").fetchall()
    return [{column: row[column] for column in columns} for row in rows]


def insert_postgres_rows(
    pg_connection: object,
    *,
    table_name: str,
    columns: Sequence[str],
    rows: Sequence[dict[str, object]],
) -> int:
    if not rows:
        return 0
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(columns)
    sql = (
        f"INSERT INTO {table_name} ({column_sql}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT DO NOTHING"
    )
    with pg_connection.cursor() as cursor:
        for row in rows:
            cursor.execute(sql, tuple(row[column] for column in columns))
    return len(rows)
