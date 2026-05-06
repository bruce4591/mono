from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ParquetExportResult:
    files_written: int
    daily_rows: int
    intraday_rows: int


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


def export_bars_to_parquet(connection: Any, *, lake_root: Path) -> ParquetExportResult:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("Parquet export requires pyarrow") from exc

    daily_rows = [_plain_dict(row) for row in _fetch_daily_bar_rows(connection)]
    intraday_rows = [_plain_dict(row) for row in _fetch_intraday_bar_rows(connection)]

    files_written = 0
    for path, rows in _daily_partitions(lake_root, daily_rows).items():
        _write_parquet(path, rows, pa=pa, pq=pq)
        files_written += 1
    for path, rows in _intraday_partitions(lake_root, intraday_rows).items():
        _write_parquet(path, rows, pa=pa, pq=pq)
        files_written += 1

    return ParquetExportResult(
        files_written=files_written,
        daily_rows=len(daily_rows),
        intraday_rows=len(intraday_rows),
    )


def _fetch_daily_bar_rows(connection: Any) -> Iterable[Any]:
    return connection.execute(
        """
        SELECT
            instrument.market,
            instrument.instrument_type AS asset_class,
            instrument.symbol,
            bar_daily.trade_date,
            bar_daily.open,
            bar_daily.high,
            bar_daily.low,
            bar_daily.close,
            bar_daily.volume_raw,
            bar_daily.turnover_raw,
            bar_daily.quote_currency,
            bar_daily.source
        FROM bar_daily
        JOIN instrument
            ON instrument.instrument_id = bar_daily.instrument_id
        ORDER BY
            instrument.market,
            instrument.instrument_type,
            instrument.symbol,
            bar_daily.trade_date
        """
    ).fetchall()


def _fetch_intraday_bar_rows(connection: Any) -> Iterable[Any]:
    return connection.execute(
        """
        SELECT
            instrument.market,
            instrument.instrument_type AS asset_class,
            instrument.symbol,
            bar_intraday.interval,
            bar_intraday.bar_start_ts_utc,
            bar_intraday.bar_end_ts_utc,
            bar_intraday.trade_date_local,
            bar_intraday.open,
            bar_intraday.high,
            bar_intraday.low,
            bar_intraday.close,
            bar_intraday.volume_raw,
            bar_intraday.turnover_raw,
            bar_intraday.is_closed_bar,
            bar_intraday.source
        FROM bar_intraday
        JOIN instrument
            ON instrument.instrument_id = bar_intraday.instrument_id
        ORDER BY
            instrument.market,
            instrument.instrument_type,
            instrument.symbol,
            bar_intraday.interval,
            bar_intraday.bar_start_ts_utc
        """
    ).fetchall()


def _daily_partitions(root: Path, rows: list[dict[str, Any]]) -> dict[Path, list[dict[str, Any]]]:
    partitions: dict[Path, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        trade_date = str(row["trade_date"])
        path = parquet_partition_path(
            root=root,
            market=str(row["market"]),
            asset_class=str(row["asset_class"]),
            symbol=str(row["symbol"]),
            interval="1d",
            year=int(trade_date[:4]),
        )
        partitions[path].append(row)
    return dict(partitions)


def _intraday_partitions(
    root: Path, rows: list[dict[str, Any]]
) -> dict[Path, list[dict[str, Any]]]:
    partitions: dict[Path, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        start_ts = _parse_utc_datetime(row["bar_start_ts_utc"])
        path = parquet_partition_path(
            root=root,
            market=str(row["market"]),
            asset_class=str(row["asset_class"]),
            symbol=str(row["symbol"]),
            interval=str(row["interval"]),
            year=start_ts.year,
            month=start_ts.month,
        )
        row["is_closed_bar"] = bool(row["is_closed_bar"])
        partitions[path].append(row)
    return dict(partitions)


def _write_parquet(path: Path, rows: list[dict[str, Any]], *, pa: Any, pq: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _plain_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        items = row.items()
    elif hasattr(row, "keys"):
        items = ((key, row[key]) for key in row.keys())
    else:
        raise TypeError(f"unsupported row type: {type(row)!r}")
    return {str(key): _plain_value(value) for key, value in items}


def _plain_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _format_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
