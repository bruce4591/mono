from __future__ import annotations

import sqlite3
from collections.abc import Iterable


def sqlite_table_counts(
    connection: sqlite3.Connection,
    table_names: Iterable[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_name in table_names:
        row = connection.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
        counts[table_name] = int(row["count"])
    return counts


def format_count_report(counts: dict[str, int]) -> str:
    lines = ["table,count"]
    for table_name in sorted(counts):
        lines.append(f"{table_name},{counts[table_name]}")
    return "\n".join(lines)
