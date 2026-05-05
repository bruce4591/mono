from __future__ import annotations

from pathlib import Path


def parquet_partition_path(
    *,
    root: Path,
    market: str,
    asset_class: str,
    symbol: str,
    interval: str,
    year: int,
    month: int | None = None,
) -> Path:
    path = (
        root
        / f"market={market}"
        / f"asset_class={asset_class}"
        / f"symbol={symbol}"
        / f"interval={interval}"
        / f"year={year:04d}"
    )
    if month is not None:
        path = path / f"month={month:02d}"
    return path / "part-000.parquet"
