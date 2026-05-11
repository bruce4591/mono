from __future__ import annotations

import sqlite3
import threading
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


class PooledPostgresConnectionAdapter(PostgresConnectionAdapter):
    def __init__(self, connection: Any, pool: PostgresConnectionPool) -> None:
        super().__init__(connection)
        self._pool = pool

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        discard = False
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        except Exception:
            discard = True
            raise
        finally:
            self._pool.release(self._connection, discard=discard)


class PostgresConnectionPool:
    def __init__(self, database_url: str, *, max_size: int = 5) -> None:
        if max_size < 1:
            raise ValueError("PostgreSQL connection pool size must be at least 1")
        self.database_url = database_url
        self.max_size = max_size
        self._available: list[Any] = []
        self._created = 0
        self._condition = threading.Condition()

    def connection(self) -> PooledPostgresConnectionAdapter:
        return PooledPostgresConnectionAdapter(self._acquire(), self)

    def release(self, connection: Any, *, discard: bool = False) -> None:
        with self._condition:
            if discard or bool(getattr(connection, "closed", False)):
                connection.close()
                self._created -= 1
            else:
                self._available.append(connection)
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            available = list(self._available)
            self._available.clear()
            self._created -= len(available)
            self._condition.notify_all()
        for connection in available:
            connection.close()

    def _acquire(self) -> Any:
        should_create = False
        with self._condition:
            while True:
                if self._available:
                    return self._available.pop()
                if self._created < self.max_size:
                    self._created += 1
                    should_create = True
                    break
                self._condition.wait()
        if should_create:
            try:
                return _connect_postgres(self.database_url)
            except Exception:
                with self._condition:
                    self._created -= 1
                    self._condition.notify()
                raise
        raise RuntimeError("unreachable PostgreSQL pool acquire state")


def connect_database_url(database_url: str) -> sqlite3.Connection | PostgresConnectionAdapter:
    if database_url.startswith("sqlite:///"):
        return connect(database_url.removeprefix("sqlite:///"))
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise ValueError(f"unsupported database URL: {database_url}")
    return PostgresConnectionAdapter(_connect_postgres(database_url))


def create_database_connector(database_url: str, *, pool_size: int = 5):
    if database_url.startswith(("postgresql://", "postgres://")):
        pool = PostgresConnectionPool(database_url, max_size=pool_size)
        return pool.connection
    return lambda: connect_database_url(database_url)


def _connect_postgres(database_url: str) -> Any:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("PostgreSQL support requires psycopg[binary]") from exc
    connection = psycopg.connect(database_url, row_factory=dict_row)
    connection.execute("SET TIME ZONE 'UTC'")
    return connection


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
        _ensure_mobile_alert_event_columns(connection)
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


def _ensure_mobile_alert_event_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(mobile_alert_event)").fetchall()
    }
    if "dedupe_key" not in columns:
        connection.execute("ALTER TABLE mobile_alert_event ADD COLUMN dedupe_key TEXT")
    if "alert_metadata" not in columns:
        connection.execute(
            "ALTER TABLE mobile_alert_event ADD COLUMN alert_metadata TEXT NOT NULL DEFAULT '{}'"
        )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mobile_alert_event_rule_dedupe
        ON mobile_alert_event (mobile_alert_rule_id, dedupe_key)
        WHERE dedupe_key IS NOT NULL
        """
    )
