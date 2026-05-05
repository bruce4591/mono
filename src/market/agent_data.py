from __future__ import annotations

from pathlib import Path


def query_symbol_history(
    *,
    lake_root: Path,
    market: str,
    asset_class: str,
    symbol: str,
    interval: str,
    start_ts_utc: str,
    end_ts_utc: str,
) -> list[dict[str, object]]:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("DuckDB support requires duckdb and pyarrow") from exc

    glob_path = (
        lake_root
        / f"market={market}"
        / f"asset_class={asset_class}"
        / f"symbol={symbol}"
        / f"interval={interval}"
        / "**"
        / "*.parquet"
    )
    sql = """
        SELECT *
        FROM read_parquet(?)
        WHERE bar_start_ts_utc >= ?
          AND bar_start_ts_utc < ?
        ORDER BY bar_start_ts_utc
    """
    with duckdb.connect(database=":memory:") as connection:
        rows = connection.execute(
            sql,
            [str(glob_path), start_ts_utc, end_ts_utc],
        ).fetchall()
        columns = [description[0] for description in connection.description]
    return [dict(zip(columns, row, strict=True)) for row in rows]
