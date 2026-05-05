from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from market.migrations import apply_sqlite_migrations


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
PG_SCHEMA_PATH = Path(__file__).with_name("pg_schema.sql")


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(db_path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(db_path), factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


class PostgresConnectionAdapter:
    backend = "postgres"

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def __enter__(self) -> "PostgresConnectionAdapter":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            self._connection.close()

    def execute(self, sql: str, params: object = ()) -> Any:
        return self._connection.execute(_translate_sqlite_placeholders(sql), params)

    def commit(self) -> None:
        self._connection.commit()


def connect_database_url(database_url: str) -> sqlite3.Connection | PostgresConnectionAdapter:
    if database_url.startswith("sqlite:///"):
        return connect(database_url.removeprefix("sqlite:///"))
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise ValueError(f"unsupported database URL: {database_url}")
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("PostgreSQL support requires psycopg[binary]") from exc
    connection = psycopg.connect(database_url, row_factory=dict_row)
    connection.execute("SET TIME ZONE 'UTC'")
    return PostgresConnectionAdapter(connection)


def _translate_sqlite_placeholders(sql: str) -> str:
    return sql.replace("?", "%s")


def init_database(db_path: Path | str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with connect(path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _ensure_push_device_columns(connection)
        _ensure_mobile_alert_rule_columns(connection)
        apply_sqlite_migrations(connection)


def init_postgres_database(database_url: str) -> None:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("PostgreSQL support requires psycopg[binary]") from exc
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(PG_SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.commit()


def _ensure_push_device_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(push_device)").fetchall()
    }
    if "getui_cid" not in columns:
        connection.execute("ALTER TABLE push_device ADD COLUMN getui_cid TEXT")
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_push_device_getui_cid
        ON push_device (getui_cid)
        WHERE getui_cid IS NOT NULL
        """
    )


def _ensure_mobile_alert_rule_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(mobile_alert_rule)").fetchall()
    }
    additions = {
        "source_type": "TEXT NOT NULL DEFAULT 'builtin'",
        "metric_key": "TEXT",
        "operator": "TEXT",
        "indicator_id": "INTEGER",
        "created_by": "TEXT NOT NULL DEFAULT 'manual'",
    }
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE mobile_alert_rule ADD COLUMN {column} {definition}")
