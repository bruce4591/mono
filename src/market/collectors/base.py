from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic, sleep as default_sleep
from typing import Callable, Protocol


@dataclass(frozen=True)
class CollectorResult:
    source_name: str
    items_synced: int
    metadata: dict[str, object] = field(default_factory=dict)


class MarketCollector(Protocol):
    source_name: str

    def sync_universe(self, connection: sqlite3.Connection) -> CollectorResult:
        raise NotImplementedError

    def sync_daily_bars(
        self,
        connection: sqlite3.Connection,
        symbols: list[str],
        days: int,
    ) -> CollectorResult:
        raise NotImplementedError

    def sync_intraday_bars(
        self,
        connection: sqlite3.Connection,
        symbols: list[str],
        interval: str,
        limit: int,
    ) -> CollectorResult:
        raise NotImplementedError

    def sync_snapshots(
        self,
        connection: sqlite3.Connection,
        symbols: list[str],
    ) -> CollectorResult:
        raise NotImplementedError


class RequestRateLimiter:
    def __init__(
        self,
        min_interval_seconds: float,
        *,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], None] = default_sleep,
    ) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.clock = clock
        self.sleep = sleep
        self._last_request_at: float | None = None

    def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            self._last_request_at = self.clock()
            return
        now = self.clock()
        if self._last_request_at is not None:
            elapsed = now - self._last_request_at
            remaining = self.min_interval_seconds - elapsed
            if remaining > 0:
                self.sleep(remaining)
                now = self.clock()
        self._last_request_at = now


def run_collector_job(
    connection: sqlite3.Connection,
    *,
    job_name: str,
    source_name: str,
    checkpoint: str,
    started_at_utc: str,
    operation,
) -> CollectorResult:
    _record_job_started(connection, job_name, checkpoint, started_at_utc)
    connection.commit()
    try:
        result = operation()
    except Exception as error:
        failed_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        _record_job_failed(connection, job_name, failed_at, str(error))
        _record_source_failed(connection, source_name, failed_at, str(error))
        connection.commit()
        raise
    _record_job_finished(connection, job_name, started_at_utc)
    _record_source_success(connection, source_name, started_at_utc)
    return result


def _record_job_started(
    connection: sqlite3.Connection,
    job_name: str,
    checkpoint: str,
    started_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO job_state (
            job_name,
            checkpoint,
            status,
            last_started_at,
            last_error,
            updated_at
        )
        VALUES (?, ?, 'running', ?, NULL, CURRENT_TIMESTAMP)
        ON CONFLICT(job_name) DO UPDATE SET
            checkpoint = excluded.checkpoint,
            status = excluded.status,
            last_started_at = excluded.last_started_at,
            last_error = NULL,
            updated_at = CURRENT_TIMESTAMP
        """,
        (job_name, checkpoint, started_at),
    )


def _record_job_finished(
    connection: sqlite3.Connection,
    job_name: str,
    finished_at: str,
) -> None:
    connection.execute(
        """
        UPDATE job_state
        SET status = 'success',
            last_finished_at = ?,
            last_error = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE job_name = ?
        """,
        (finished_at, job_name),
    )


def _record_job_failed(
    connection: sqlite3.Connection,
    job_name: str,
    failed_at: str,
    error: str,
) -> None:
    connection.execute(
        """
        UPDATE job_state
        SET status = 'failed',
            last_finished_at = ?,
            last_error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE job_name = ?
        """,
        (failed_at, error, job_name),
    )


def _record_source_success(
    connection: sqlite3.Connection,
    source_name: str,
    success_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO source_health (
            source_name,
            status,
            last_success_at,
            last_error,
            updated_at
        )
        VALUES (?, 'ok', ?, NULL, CURRENT_TIMESTAMP)
        ON CONFLICT(source_name) DO UPDATE SET
            status = excluded.status,
            last_success_at = excluded.last_success_at,
            last_error = NULL,
            updated_at = CURRENT_TIMESTAMP
        """,
        (source_name, success_at),
    )


def _record_source_failed(
    connection: sqlite3.Connection,
    source_name: str,
    failed_at: str,
    error: str,
) -> None:
    connection.execute(
        """
        INSERT INTO source_health (
            source_name,
            status,
            last_error_at,
            last_error,
            updated_at
        )
        VALUES (?, 'failed', ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source_name) DO UPDATE SET
            status = excluded.status,
            last_error_at = excluded.last_error_at,
            last_error = excluded.last_error,
            updated_at = CURRENT_TIMESTAMP
        """,
        (source_name, failed_at, error),
    )
