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
