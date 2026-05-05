# Market Data Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the market data layer toward PostgreSQL for online state and Parquet/DuckDB for historical Agent analysis without breaking the current SQLite-backed app.

**Architecture:** First add versioned migrations and split latest snapshots from snapshot history while still using SQLite. Then introduce database URL based connection handling, PostgreSQL schema support, a backfill command, and Parquet/DuckDB export/query helpers behind explicit commands. PostgreSQL remains the online source of truth; Parquet is an analytical mirror.

**Tech Stack:** Python 3.11, stdlib `sqlite3`, planned `psycopg[binary]`, planned `duckdb`, planned `pyarrow`, current `market` package, `unittest`, PostgreSQL, Parquet.

---

## Non-Negotiable Data Safety Rules

- No production cutover is allowed without a verified SQLite backup.
- No migration may delete or truncate production SQLite data.
- PostgreSQL backfill must be additive and idempotent.
- Every backfill run must produce a row-count report for each copied table.
- Cutover requires source SQLite counts and target PostgreSQL counts to match for every online table.
- Keep SQLite as the rollback source until PostgreSQL has passed production read validation.
- Do not remove legacy `market_snapshot` during this plan. New tables must be populated alongside it.
- Parquet export is a derived analytical copy. A failed Parquet export must never block or replace PostgreSQL/SQLite online data.
- If any checksum, count, or API validation fails, stop and keep the service on SQLite.

## File Structure

**Schema and database access**
- Modify: `src/market/db.py` - connection factory, migration runner, database URL parsing.
- Modify: `src/market/schema.sql` - current SQLite schema with latest/history snapshot tables and refresh state.
- Create: `src/market/migrations.py` - ordered migration definitions and migration execution helpers.
- Create: `src/market/pg_schema.sql` - PostgreSQL DDL matching the online schema.
- Test: `tests/test_db_schema.py` - schema, migration, and compatibility tests.

**Snapshot and refresh repositories**
- Modify: `src/market/repositories.py` - write latest snapshot and history snapshot rows.
- Modify: `src/market/api.py` - board open refresh should use durable `board_refresh_state`.
- Test: `tests/test_repositories.py`
- Test: `tests/test_api.py`

**PostgreSQL migration and backfill**
- Modify: `pyproject.toml` - add PostgreSQL dependency after user approval during execution.
- Modify: `src/market/cli.py` - add PostgreSQL schema init and SQLite-to-PostgreSQL backfill commands.
- Create: `src/market/backfill.py` - table-by-table backfill helpers.
- Create: `src/market/data_integrity.py` - row count and checksum helpers used before cutover.
- Test: `tests/test_cli.py`
- Test: `tests/test_backfill.py`
- Test: `tests/test_data_integrity.py`

**Parquet and Agent analysis layer**
- Modify: `pyproject.toml` - add DuckDB/Parquet dependencies after user approval during execution.
- Create: `src/market/parquet_export.py` - export daily/intraday bars into partitioned Parquet.
- Create: `src/market/agent_data.py` - read-only DuckDB query helpers for historical Agent access.
- Test: `tests/test_parquet_export.py`
- Test: `tests/test_agent_data.py`

**Docs and deployment**
- Modify: `docs/deploy_tencent_lighthouse.md` - PostgreSQL setup, env vars, backup, cutover, rollback.
- Create: `deploy/scripts/market-backfill-postgres.sh`
- Create: `deploy/scripts/market-export-parquet.sh`
- Test: `tests/test_deploy_config.py`

## Execution Rules

- Do not cut production over to PostgreSQL until the SQLite path still passes all existing tests.
- Ask before adding new dependencies to `pyproject.toml`.
- Keep SQLite supported until the server has been backed up and backfilled.
- Prefer additive migrations. Do not delete `market_snapshot` until all reads and writes have moved to `latest_market_snapshot` and `market_snapshot_history`.
- Treat SQLite as the source of truth until a complete PostgreSQL backfill has been verified by row counts and checksums.
- Do not run destructive commands against the production SQLite database.
- Commit after each task.

---

## Phase 1: Versioned Migrations and Snapshot Split on SQLite

### Task 1: Add Migration Tracking

**Files:**
- Create: `src/market/migrations.py`
- Modify: `src/market/db.py`
- Test: `tests/test_db_schema.py`

- [x] **Step 1: Write a failing schema migration test**

Add this test to `tests/test_db_schema.py`:

```python
def test_schema_migrations_are_recorded(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "market.sqlite3"
        init_database(db_path)
        with connect(db_path) as connection:
            rows = connection.execute(
                """
                SELECT migration_id
                FROM schema_migration
                ORDER BY migration_id
                """
            ).fetchall()
    self.assertGreaterEqual(len(rows), 1)
    self.assertEqual(str(rows[0]["migration_id"]), "0001_initial_schema")
```

- [x] **Step 2: Run the failing test**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_db_schema.TestDatabaseSchema.test_schema_migrations_are_recorded -v
```

Expected: FAIL with `no such table: schema_migration`.

- [x] **Step 3: Create the migration helper**

Create `src/market/migrations.py`:

```python
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
    rows = connection.execute(
        "SELECT migration_id FROM schema_migration"
    ).fetchall()
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
```

- [x] **Step 4: Run migrations from database initialization**

Modify `src/market/db.py`:

```python
from market.migrations import apply_sqlite_migrations
```

Then update `init_database`:

```python
def init_database(db_path: Path | str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with connect(path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _ensure_push_device_columns(connection)
        _ensure_mobile_alert_rule_columns(connection)
        apply_sqlite_migrations(connection)
```

- [x] **Step 5: Run the migration test**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_db_schema.TestDatabaseSchema.test_schema_migrations_are_recorded -v
```

Expected: PASS.

- [x] **Step 6: Run all DB schema tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_db_schema -v
```

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/market/migrations.py src/market/db.py tests/test_db_schema.py
git commit -m "Add database migration tracking"
```

### Task 2: Add Latest Snapshot and Snapshot History Tables

**Files:**
- Modify: `src/market/schema.sql`
- Modify: `src/market/migrations.py`
- Test: `tests/test_db_schema.py`

- [x] **Step 1: Write a failing table existence test**

Add this test to `tests/test_db_schema.py`:

```python
def test_latest_and_history_snapshot_tables_exist(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "market.sqlite3"
        init_database(db_path)
        with connect(db_path) as connection:
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
    self.assertIn("latest_market_snapshot", tables)
    self.assertIn("market_snapshot_history", tables)
```

- [x] **Step 2: Run the failing test**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_db_schema.TestDatabaseSchema.test_latest_and_history_snapshot_tables_exist -v
```

Expected: FAIL because the new tables do not exist.

- [x] **Step 3: Add the new tables to `schema.sql`**

Add after `market_snapshot` in `src/market/schema.sql`:

```sql
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
```

Add indexes near the existing snapshot indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_latest_market_snapshot_turnover
    ON latest_market_snapshot (trade_date_local, turnover_raw DESC);

CREATE INDEX IF NOT EXISTS idx_market_snapshot_history_lookup
    ON market_snapshot_history (instrument_id, snapshot_ts_utc DESC);
```

- [x] **Step 4: Add an idempotent migration for existing databases**

Append this migration to `SQLITE_MIGRATIONS` in `src/market/migrations.py`:

```python
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
```

- [x] **Step 5: Run the table test**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_db_schema.TestDatabaseSchema.test_latest_and_history_snapshot_tables_exist -v
```

Expected: PASS.

- [x] **Step 6: Add a migration backfill test**

Add this test to `tests/test_db_schema.py`:

```python
def test_snapshot_split_migration_backfills_existing_snapshot(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "market.sqlite3"
        init_database(db_path)
        with connect(db_path) as connection:
            instrument_id = connection.execute(
                """
                INSERT INTO instrument (
                    market, symbol, display_name, exchange, instrument_type,
                    quote_currency, timezone
                )
                VALUES ('HK', '00700', 'Tencent', 'HKEX', 'stock', 'HKD', 'Asia/Hong_Kong')
                RETURNING instrument_id
                """
            ).fetchone()["instrument_id"]
            connection.execute(
                """
                INSERT OR REPLACE INTO market_snapshot (
                    instrument_id, snapshot_ts_utc, trade_date_local, last_price,
                    change_pct, volume_raw, turnover_raw, quote_currency, source
                )
                VALUES (?, '2026-05-05T03:01:04Z', '2026-05-05', 468.2, 1.2, 10, 20, 'HKD', 'akshare')
                """,
                (instrument_id,),
            )
            connection.execute(
                "DELETE FROM schema_migration WHERE migration_id = '0002_split_market_snapshots'"
            )
            from market.migrations import apply_sqlite_migrations

            apply_sqlite_migrations(connection)
            latest = connection.execute(
                "SELECT last_price FROM latest_market_snapshot WHERE instrument_id = ?",
                (instrument_id,),
            ).fetchone()
            history = connection.execute(
                "SELECT last_price FROM market_snapshot_history WHERE instrument_id = ?",
                (instrument_id,),
            ).fetchone()
    self.assertEqual(float(latest["last_price"]), 468.2)
    self.assertEqual(float(history["last_price"]), 468.2)
```

- [x] **Step 7: Run DB schema tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_db_schema -v
```

Expected: PASS.

- [x] **Step 8: Commit**

```bash
git add src/market/schema.sql src/market/migrations.py tests/test_db_schema.py
git commit -m "Split market snapshots into latest and history tables"
```

### Task 3: Write Snapshots to Both Latest and History Tables

**Files:**
- Modify: `src/market/repositories.py`
- Test: `tests/test_repositories.py`

- [x] **Step 1: Write a failing repository test**

Add this test to `tests/test_repositories.py`:

```python
def test_market_snapshot_upsert_writes_latest_and_history(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "market.sqlite3"
        init_database(db_path)
        with connect(db_path) as connection:
            instruments = InstrumentRepository(connection)
            instrument_id = instruments.upsert(
                Instrument(
                    instrument_id=None,
                    market="HK",
                    symbol="00700",
                    display_name="Tencent",
                    exchange="HKEX",
                    instrument_type="stock",
                    quote_currency="HKD",
                    timezone="Asia/Hong_Kong",
                )
            )
            snapshots = MarketSnapshotRepository(connection)
            snapshots.upsert(
                MarketSnapshot(
                    instrument_id=instrument_id,
                    snapshot_ts_utc="2026-05-05T03:01:04Z",
                    trade_date_local="2026-05-05",
                    last_price=468.2,
                    change_pct=1.2,
                    volume_raw=10.0,
                    turnover_raw=20.0,
                    quote_currency="HKD",
                    source="akshare",
                )
            )
            snapshots.upsert(
                MarketSnapshot(
                    instrument_id=instrument_id,
                    snapshot_ts_utc="2026-05-05T03:02:04Z",
                    trade_date_local="2026-05-05",
                    last_price=469.0,
                    change_pct=1.3,
                    volume_raw=11.0,
                    turnover_raw=22.0,
                    quote_currency="HKD",
                    source="akshare",
                )
            )
            latest = connection.execute(
                "SELECT last_price, snapshot_ts_utc FROM latest_market_snapshot WHERE instrument_id = ?",
                (instrument_id,),
            ).fetchone()
            history_count = connection.execute(
                "SELECT COUNT(*) AS count FROM market_snapshot_history WHERE instrument_id = ?",
                (instrument_id,),
            ).fetchone()
    self.assertEqual(float(latest["last_price"]), 469.0)
    self.assertEqual(str(latest["snapshot_ts_utc"]), "2026-05-05T03:02:04Z")
    self.assertEqual(int(history_count["count"]), 2)
```

- [x] **Step 2: Run the failing test**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_repositories.TestRepositories.test_market_snapshot_upsert_writes_latest_and_history -v
```

Expected: FAIL because `MarketSnapshotRepository.upsert` does not write both new tables.

- [x] **Step 3: Update `MarketSnapshotRepository.upsert`**

Modify the existing `upsert` method in `src/market/repositories.py` so it keeps writing the legacy table and additionally writes the new tables:

```python
def upsert(self, snapshot: MarketSnapshot) -> None:
    params = (
        snapshot.instrument_id,
        snapshot.snapshot_ts_utc,
        snapshot.trade_date_local,
        snapshot.last_price,
        snapshot.change_pct,
        snapshot.volume_raw,
        snapshot.turnover_raw,
        snapshot.quote_currency,
        snapshot.source,
    )
    self.connection.execute(
        """
        INSERT INTO market_snapshot (
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(instrument_id, trade_date_local) DO UPDATE SET
            snapshot_ts_utc = excluded.snapshot_ts_utc,
            last_price = excluded.last_price,
            change_pct = excluded.change_pct,
            volume_raw = excluded.volume_raw,
            turnover_raw = excluded.turnover_raw,
            quote_currency = excluded.quote_currency,
            source = excluded.source,
            updated_at = CURRENT_TIMESTAMP
        """,
        params,
    )
    self.connection.execute(
        """
        INSERT INTO latest_market_snapshot (
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(instrument_id) DO UPDATE SET
            snapshot_ts_utc = excluded.snapshot_ts_utc,
            trade_date_local = excluded.trade_date_local,
            last_price = excluded.last_price,
            change_pct = excluded.change_pct,
            volume_raw = excluded.volume_raw,
            turnover_raw = excluded.turnover_raw,
            quote_currency = excluded.quote_currency,
            source = excluded.source,
            updated_at = CURRENT_TIMESTAMP
        """,
        params,
    )
    self.connection.execute(
        """
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        params,
    )
```

- [x] **Step 4: Run the repository test**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_repositories.TestRepositories.test_market_snapshot_upsert_writes_latest_and_history -v
```

Expected: PASS.

- [x] **Step 5: Run affected tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_repositories tests.test_api tests.test_cli -v
```

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add src/market/repositories.py tests/test_repositories.py
git commit -m "Write latest and history market snapshots"
```

### Task 4: Add Durable Board Refresh State

**Files:**
- Modify: `src/market/schema.sql`
- Modify: `src/market/migrations.py`
- Modify: `src/market/api.py`
- Test: `tests/test_api.py`
- Test: `tests/test_db_schema.py`

- [x] **Step 1: Write a failing schema test**

Add this to `tests/test_db_schema.py`:

```python
def test_board_refresh_state_table_exists(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "market.sqlite3"
        init_database(db_path)
        with connect(db_path) as connection:
            row = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'board_refresh_state'
                """
            ).fetchone()
    self.assertIsNotNone(row)
```

- [x] **Step 2: Add the table and migration**

Add this table to `src/market/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS board_refresh_state (
    board_name TEXT PRIMARY KEY,
    last_requested_at_utc TEXT,
    last_started_at_utc TEXT,
    last_finished_at_utc TEXT,
    status TEXT NOT NULL DEFAULT 'idle',
    last_error TEXT,
    updated_at_utc TEXT NOT NULL
);
```

Append this migration in `src/market/migrations.py`:

```python
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
```

- [x] **Step 3: Add refresh reservation helpers**

Add helper functions near the board refresh code in `src/market/api.py`:

```python
def _reserve_durable_board_refresh(
    connection: sqlite3.Connection,
    *,
    board_name: str,
    now_utc: str,
    ttl_seconds: int,
) -> bool:
    row = connection.execute(
        """
        SELECT last_requested_at_utc, status
        FROM board_refresh_state
        WHERE board_name = ?
        """,
        (board_name,),
    ).fetchone()
    if row is not None and row["last_requested_at_utc"]:
        previous = _parse_utc(str(row["last_requested_at_utc"]))
        current = _parse_utc(now_utc)
        if (current - previous).total_seconds() < ttl_seconds:
            return False
    connection.execute(
        """
        INSERT INTO board_refresh_state (
            board_name,
            last_requested_at_utc,
            last_started_at_utc,
            status,
            updated_at_utc
        )
        VALUES (?, ?, ?, 'running', ?)
        ON CONFLICT(board_name) DO UPDATE SET
            last_requested_at_utc = excluded.last_requested_at_utc,
            last_started_at_utc = excluded.last_started_at_utc,
            status = 'running',
            last_error = NULL,
            updated_at_utc = excluded.updated_at_utc
        """,
        (board_name, now_utc, now_utc, now_utc),
    )
    return True
```

Add `_parse_utc` if no equivalent helper exists:

```python
def _parse_utc(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
```

- [x] **Step 4: Replace the in-process one-minute reservation path**

Update `schedule_board_prices_refresh_on_open` so it checks `board_refresh_state` before starting a refresh thread. Keep the existing in-process guard as a secondary protection during this task.

- [x] **Step 5: Add a durable TTL API test**

Add this to `tests/test_api.py`:

```python
def test_board_open_refresh_uses_durable_refresh_state(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "market.sqlite3"
        init_database(db_path)
        with connect(db_path) as connection:
            first = _reserve_durable_board_refresh(
                connection,
                board_name="HK_STOCK_FOCUS20",
                now_utc="2026-05-05T03:01:04+00:00",
                ttl_seconds=60,
            )
            second = _reserve_durable_board_refresh(
                connection,
                board_name="HK_STOCK_FOCUS20",
                now_utc="2026-05-05T03:01:30+00:00",
                ttl_seconds=60,
            )
    self.assertTrue(first)
    self.assertFalse(second)
```

- [x] **Step 6: Run affected tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_db_schema tests.test_api -v
```

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/market/schema.sql src/market/migrations.py src/market/api.py tests/test_api.py tests/test_db_schema.py
git commit -m "Persist board refresh throttling state"
```

---

## Phase 2: PostgreSQL Online Store

### Task 5: Add Database URL Settings Without Changing Runtime Behavior

**Files:**
- Modify: `src/market/settings.py`
- Test: `tests/test_settings.py`

- [x] **Step 1: Write failing settings tests**

Add to `tests/test_settings.py`:

```python
def test_load_settings_defaults_to_sqlite_database_url(self):
    with patch.dict(os.environ, {}, clear=True):
        settings = load_settings()
    self.assertEqual(settings.database_url, "sqlite:///data/market.sqlite3")
    self.assertEqual(settings.db_path, Path("data/market.sqlite3"))


def test_load_settings_accepts_postgres_database_url(self):
    with patch.dict(
        os.environ,
        {"MARKET_DATABASE_URL": "postgresql://market:secret@localhost:5432/market"},
        clear=True,
    ):
        settings = load_settings()
    self.assertEqual(settings.database_url, "postgresql://market:secret@localhost:5432/market")
    self.assertIsNone(settings.db_path)
```

- [x] **Step 2: Update settings model**

Modify `src/market/settings.py`:

```python
@dataclass(frozen=True)
class Settings:
    database_url: str
    db_path: Path | None
    log_level: str
```

Update `load_settings`:

```python
def load_settings() -> Settings:
    database_url = os.environ.get("MARKET_DATABASE_URL")
    legacy_db_path = os.environ.get("MARKET_DB_PATH")
    if database_url is None:
        db_path = Path(legacy_db_path or "data/market.sqlite3")
        database_url = f"sqlite:///{db_path}"
    else:
        db_path = None
        if database_url.startswith("sqlite:///"):
            db_path = Path(database_url.removeprefix("sqlite:///"))
    log_level = os.environ.get("MARKET_LOG_LEVEL", "INFO")
    return Settings(database_url=database_url, db_path=db_path, log_level=log_level)
```

- [x] **Step 3: Run settings tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_settings -v
```

Expected: PASS.

- [x] **Step 4: Run API smoke tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_api -v
```

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/market/settings.py tests/test_settings.py
git commit -m "Add database URL settings"
```

### Task 6: Add PostgreSQL Schema File

**Files:**
- Create: `src/market/pg_schema.sql`
- Test: `tests/test_db_schema.py`

- [x] **Step 1: Write a static PostgreSQL schema test**

Add to `tests/test_db_schema.py`:

```python
def test_postgres_schema_contains_online_tables(self):
    schema = (Path(__file__).resolve().parents[1] / "src" / "market" / "pg_schema.sql").read_text(
        encoding="utf-8"
    )
    self.assertIn("CREATE TABLE IF NOT EXISTS instrument", schema)
    self.assertIn("CREATE TABLE IF NOT EXISTS latest_market_snapshot", schema)
    self.assertIn("CREATE TABLE IF NOT EXISTS market_snapshot_history", schema)
    self.assertIn("CREATE TABLE IF NOT EXISTS board_refresh_state", schema)
    self.assertIn("CREATE TABLE IF NOT EXISTS mobile_alert_delivery", schema)
    self.assertIn("CREATE TABLE IF NOT EXISTS schema_migration", schema)
```

- [x] **Step 2: Create PostgreSQL schema**

Create `src/market/pg_schema.sql`. Use the same logical tables as `schema.sql`, with PostgreSQL syntax:

```sql
CREATE TABLE IF NOT EXISTS schema_migration (
    migration_id TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS instrument (
    instrument_id BIGSERIAL PRIMARY KEY,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    display_name TEXT NOT NULL,
    exchange TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    quote_currency TEXT NOT NULL,
    timezone TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    extra_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (market, symbol)
);

CREATE TABLE IF NOT EXISTS latest_market_snapshot (
    instrument_id BIGINT PRIMARY KEY REFERENCES instrument(instrument_id),
    snapshot_ts_utc TIMESTAMPTZ NOT NULL,
    trade_date_local DATE NOT NULL,
    last_price DOUBLE PRECISION,
    change_pct DOUBLE PRECISION,
    volume_raw DOUBLE PRECISION,
    turnover_raw DOUBLE PRECISION,
    quote_currency TEXT NOT NULL,
    source TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_snapshot_history (
    instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id),
    snapshot_ts_utc TIMESTAMPTZ NOT NULL,
    trade_date_local DATE NOT NULL,
    last_price DOUBLE PRECISION,
    change_pct DOUBLE PRECISION,
    volume_raw DOUBLE PRECISION,
    turnover_raw DOUBLE PRECISION,
    quote_currency TEXT NOT NULL,
    source TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (instrument_id, snapshot_ts_utc)
);

CREATE TABLE IF NOT EXISTS board_refresh_state (
    board_name TEXT PRIMARY KEY,
    last_requested_at_utc TIMESTAMPTZ,
    last_started_at_utc TIMESTAMPTZ,
    last_finished_at_utc TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'idle',
    last_error TEXT,
    updated_at_utc TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_latest_market_snapshot_turnover
    ON latest_market_snapshot (trade_date_local, turnover_raw DESC);

CREATE INDEX IF NOT EXISTS idx_market_snapshot_history_lookup
    ON market_snapshot_history (instrument_id, snapshot_ts_utc DESC);
```

Then copy the remaining online tables from `schema.sql` into PostgreSQL syntax:

- `bar_daily`
- `bar_intraday`
- `ranking_snapshot`
- `watchlist`
- `job_state`
- `source_health`
- `alert_rule`
- `alert_event`
- `push_device`
- `mobile_alert_rule`
- `indicator_definition`
- `indicator_value`
- `mobile_alert_event`
- `mobile_alert_delivery`
- `device_checkpoint`
- `device_session`

Use `BIGSERIAL` for autoincrement IDs, `BOOLEAN` for boolean fields, `TIMESTAMPTZ` for UTC timestamps, `JSONB` for metadata payloads, and `ON CONFLICT` compatible unique constraints.

- [x] **Step 3: Run schema static test**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_db_schema.TestDatabaseSchema.test_postgres_schema_contains_online_tables -v
```

Expected: PASS.

- [x] **Step 4: Commit**

```bash
git add src/market/pg_schema.sql tests/test_db_schema.py
git commit -m "Add PostgreSQL online schema"
```

### Task 7: Add PostgreSQL Dependency and Init Command

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/market/db.py`
- Modify: `src/market/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Get dependency approval**

Before editing `pyproject.toml`, ask the user to approve:

```text
需要新增 PostgreSQL 驱动依赖 psycopg[binary]>=3.2,<4。是否批准？
```

- [ ] **Step 2: Add dependency after approval**

Modify `pyproject.toml`:

```toml
dependencies = [
    "akshare>=1.18,<2",
    "websocket-client>=1.8,<2",
    "psycopg[binary]>=3.2,<4",
]
```

- [ ] **Step 3: Add PostgreSQL schema initializer**

Modify `src/market/db.py`:

```python
PG_SCHEMA_PATH = Path(__file__).with_name("pg_schema.sql")


def init_postgres_database(database_url: str) -> None:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("PostgreSQL support requires psycopg[binary]") from exc
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(PG_SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.commit()
```

- [ ] **Step 4: Add CLI parser command**

Modify `src/market/cli.py` to add:

```python
init_pg_parser = subparsers.add_parser("init-postgres-db")
init_pg_parser.add_argument("--database-url", required=True)
init_pg_parser.set_defaults(func=_handle_init_postgres_db)
```

Add handler:

```python
def _handle_init_postgres_db(args: argparse.Namespace) -> int:
    init_postgres_database(args.database_url)
    return 0
```

- [ ] **Step 5: Add CLI test for parser wiring**

Add to `tests/test_cli.py`:

```python
def test_init_postgres_db_requires_database_url(self):
    parser = build_parser()
    with self.assertRaises(SystemExit):
        parser.parse_args(["init-postgres-db"])
```

- [ ] **Step 6: Run affected tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_cli tests.test_db_schema -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/market/db.py src/market/cli.py tests/test_cli.py
git commit -m "Add PostgreSQL database initialization"
```

### Task 8: Add SQLite to PostgreSQL Backfill Command

**Files:**
- Create: `src/market/backfill.py`
- Modify: `src/market/cli.py`
- Test: `tests/test_backfill.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write a unit test for row ordering**

Create `tests/test_backfill.py`:

```python
from __future__ import annotations

import unittest

from market.backfill import ONLINE_BACKFILL_TABLES


class BackfillTests(unittest.TestCase):
    def test_backfill_tables_start_with_parent_tables(self):
        self.assertEqual(ONLINE_BACKFILL_TABLES[0], "instrument")
        self.assertLess(
            ONLINE_BACKFILL_TABLES.index("instrument"),
            ONLINE_BACKFILL_TABLES.index("bar_daily"),
        )
        self.assertLess(
            ONLINE_BACKFILL_TABLES.index("push_device"),
            ONLINE_BACKFILL_TABLES.index("mobile_alert_delivery"),
        )
```

- [ ] **Step 2: Create backfill helper**

Create `src/market/backfill.py`:

```python
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
```

- [ ] **Step 3: Add PostgreSQL insert helper**

Add to `src/market/backfill.py`:

```python
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
    updates = ", ".join([f"{column} = EXCLUDED.{column}" for column in columns])
    sql = (
        f"INSERT INTO {table_name} ({column_sql}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT DO NOTHING"
    )
    with pg_connection.cursor() as cursor:
        for row in rows:
            cursor.execute(sql, tuple(row[column] for column in columns))
    return len(rows)
```

- [ ] **Step 4: Add backfill command handler**

Modify `src/market/cli.py`:

```python
backfill_parser = subparsers.add_parser("backfill-postgres")
backfill_parser.add_argument("--sqlite-db", required=True)
backfill_parser.add_argument("--postgres-url", required=True)
backfill_parser.set_defaults(func=_handle_backfill_postgres)
```

Add handler:

```python
def _handle_backfill_postgres(args: argparse.Namespace) -> int:
    from market.backfill import ONLINE_BACKFILL_TABLES, fetch_sqlite_rows, insert_postgres_rows, list_table_columns
    import psycopg

    with connect(Path(args.sqlite_db)) as sqlite_connection:
        with psycopg.connect(args.postgres_url) as pg_connection:
            for table_name in ONLINE_BACKFILL_TABLES:
                columns = list_table_columns(sqlite_connection, table_name)
                rows = fetch_sqlite_rows(
                    sqlite_connection,
                    table_name=table_name,
                    columns=columns,
                )
                insert_postgres_rows(
                    pg_connection,
                    table_name=table_name,
                    columns=columns,
                    rows=rows,
                )
            pg_connection.commit()
    return 0
```

- [ ] **Step 5: Run tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_backfill tests.test_cli -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/market/backfill.py src/market/cli.py tests/test_backfill.py tests/test_cli.py
git commit -m "Add SQLite to PostgreSQL backfill command"
```

### Task 9: Add Data Integrity Report Before Cutover

**Files:**
- Create: `src/market/data_integrity.py`
- Modify: `src/market/cli.py`
- Test: `tests/test_data_integrity.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write a row count report test**

Create `tests/test_data_integrity.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.data_integrity import sqlite_table_counts
from market.db import connect, init_database


class DataIntegrityTests(unittest.TestCase):
    def test_sqlite_table_counts_include_core_tables(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)
            with connect(db_path) as connection:
                counts = sqlite_table_counts(connection, ["instrument", "push_device"])
        self.assertEqual(counts["instrument"], 0)
        self.assertEqual(counts["push_device"], 0)
```

- [ ] **Step 2: Create integrity helpers**

Create `src/market/data_integrity.py`:

```python
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
```

- [ ] **Step 3: Add CLI report command**

Modify `src/market/cli.py`:

```python
integrity_parser = subparsers.add_parser("sqlite-count-report")
integrity_parser.add_argument("--sqlite-db", required=True)
integrity_parser.set_defaults(func=_handle_sqlite_count_report)
```

Add handler:

```python
def _handle_sqlite_count_report(args: argparse.Namespace) -> int:
    from market.backfill import ONLINE_BACKFILL_TABLES
    from market.data_integrity import format_count_report, sqlite_table_counts

    with connect(Path(args.sqlite_db)) as connection:
        counts = sqlite_table_counts(connection, ONLINE_BACKFILL_TABLES)
    print(format_count_report(counts))
    return 0
```

- [ ] **Step 4: Add CLI parser test**

Add to `tests/test_cli.py`:

```python
def test_sqlite_count_report_requires_sqlite_db(self):
    parser = build_parser()
    with self.assertRaises(SystemExit):
        parser.parse_args(["sqlite-count-report"])
```

- [ ] **Step 5: Run tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_data_integrity tests.test_cli -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/market/data_integrity.py src/market/cli.py tests/test_data_integrity.py tests/test_cli.py
git commit -m "Add data integrity reports for database cutover"
```

---

## Phase 3: Parquet/DuckDB Historical Layer

### Task 10: Add Parquet Export Path Builder

**Files:**
- Create: `src/market/parquet_export.py`
- Test: `tests/test_parquet_export.py`

- [ ] **Step 1: Write a path builder test**

Create `tests/test_parquet_export.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path

from market.parquet_export import parquet_partition_path


class ParquetExportTests(unittest.TestCase):
    def test_partition_path_for_monthly_intraday_symbol(self):
        path = parquet_partition_path(
            root=Path("/data/market-lake"),
            market="HK",
            asset_class="stock",
            symbol="00700",
            interval="1m",
            year=2026,
            month=5,
        )
        self.assertEqual(
            path,
            Path(
                "/data/market-lake/market=HK/asset_class=stock/symbol=00700/"
                "interval=1m/year=2026/month=05/part-000.parquet"
            ),
        )
```

- [ ] **Step 2: Implement path builder**

Create `src/market/parquet_export.py`:

```python
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
```

- [ ] **Step 3: Run test**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_parquet_export -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/market/parquet_export.py tests/test_parquet_export.py
git commit -m "Add Parquet partition path helper"
```

### Task 11: Add DuckDB Read Helper

**Files:**
- Modify: `pyproject.toml`
- Create: `src/market/agent_data.py`
- Test: `tests/test_agent_data.py`

- [ ] **Step 1: Get dependency approval**

Before editing `pyproject.toml`, ask:

```text
需要新增历史分析依赖 duckdb>=1.2,<2 和 pyarrow>=18,<19。是否批准？
```

- [ ] **Step 2: Add dependencies after approval**

Modify `pyproject.toml`:

```toml
dependencies = [
    "akshare>=1.18,<2",
    "websocket-client>=1.8,<2",
    "psycopg[binary]>=3.2,<4",
    "duckdb>=1.2,<2",
    "pyarrow>=18,<19",
]
```

- [ ] **Step 3: Create Agent data helper**

Create `src/market/agent_data.py`:

```python
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
```

- [ ] **Step 4: Add import error test**

Add to `tests/test_agent_data.py`:

```python
from __future__ import annotations

import builtins
import unittest
from pathlib import Path
from unittest.mock import patch

from market.agent_data import query_symbol_history


class AgentDataTests(unittest.TestCase):
    def test_query_symbol_history_reports_missing_duckdb(self):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "duckdb":
                raise ImportError("missing duckdb")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaisesRegex(RuntimeError, "DuckDB support requires"):
                query_symbol_history(
                    lake_root=Path("/tmp/lake"),
                    market="HK",
                    asset_class="stock",
                    symbol="00700",
                    interval="1m",
                    start_ts_utc="2026-05-05T01:30:00Z",
                    end_ts_utc="2026-05-05T02:30:00Z",
                )
```

- [ ] **Step 5: Run tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_agent_data -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/market/agent_data.py tests/test_agent_data.py
git commit -m "Add DuckDB agent data helper"
```

---

## Phase 4: Deployment and Cutover

### Task 12: Add PostgreSQL Deployment Notes

**Files:**
- Modify: `docs/deploy_tencent_lighthouse.md`
- Create: `deploy/scripts/market-backfill-postgres.sh`
- Test: `tests/test_deploy_config.py`

- [ ] **Step 1: Add deployment script test**

Add to `tests/test_deploy_config.py`:

```python
def test_postgres_backfill_script_exists(self):
    script = Path("deploy/scripts/market-backfill-postgres.sh")
    self.assertTrue(script.exists())
    content = script.read_text(encoding="utf-8")
    self.assertIn("market backfill-postgres", content)
    self.assertIn("MARKET_DATABASE_URL", content)
```

- [ ] **Step 2: Create backfill script**

Create `deploy/scripts/market-backfill-postgres.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

cd /home/ubuntu/github/mono

if [[ -z "${MARKET_DATABASE_URL:-}" ]]; then
  echo "MARKET_DATABASE_URL is required" >&2
  exit 1
fi

SQLITE_DB="${MARKET_DB_PATH:-/home/ubuntu/github/mono/data/market.sqlite3}"

PYTHONPATH=src .venv/bin/market init-postgres-db --database-url "$MARKET_DATABASE_URL"
PYTHONPATH=src .venv/bin/market sqlite-count-report --sqlite-db "$SQLITE_DB" \
  | tee /home/ubuntu/github/mono/logs/sqlite-count-report.$(date +%Y%m%d%H%M%S).csv
PYTHONPATH=src .venv/bin/market backfill-postgres \
  --sqlite-db "$SQLITE_DB" \
  --postgres-url "$MARKET_DATABASE_URL"
```

- [ ] **Step 3: Update deployment docs**

Add a section to `docs/deploy_tencent_lighthouse.md`:

```markdown
## PostgreSQL Cutover

1. Back up the current SQLite database:

```bash
cp /home/ubuntu/github/mono/data/market.sqlite3 \
  /home/ubuntu/github/mono/data/market.sqlite3.$(date +%Y%m%d%H%M%S).bak
sqlite3 /home/ubuntu/github/mono/data/market.sqlite3 "PRAGMA integrity_check;"
```

2. Create PostgreSQL database and user.
3. Set `MARKET_DATABASE_URL` in `/home/ubuntu/github/mono/.market.env`.
4. Run:

```bash
/home/ubuntu/github/mono/deploy/scripts/market-backfill-postgres.sh
```

5. Compare the saved SQLite count report with PostgreSQL row counts.
6. Restart `market-api.service` only after counts match.
7. Verify `/api/status`, `/api/boards/HK_STOCK_FOCUS20`, and a detail API.
8. Roll back by removing `MARKET_DATABASE_URL` and restarting the service.
```

- [ ] **Step 4: Run deployment tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_deploy_config -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/deploy_tencent_lighthouse.md deploy/scripts/market-backfill-postgres.sh tests/test_deploy_config.py
git commit -m "Document PostgreSQL cutover"
```

### Task 13: Server Validation Before Production Cutover

**Files:**
- No code changes expected.

- [ ] **Step 1: Push branch**

Run locally:

```bash
git push origin codex/market-data-platform
```

Expected: branch updates successfully.

- [ ] **Step 2: Update server branch**

Run:

```bash
ssh tencent-market 'cd /home/ubuntu/github/mono && git fetch origin && git merge --ff-only origin/codex/market-data-platform'
```

Expected: fast-forward or already up to date.

- [ ] **Step 3: Run server tests against SQLite**

Run:

```bash
ssh tencent-market 'cd /home/ubuntu/github/mono && PYTHONPATH=src .venv/bin/python -m unittest tests.test_db_schema tests.test_repositories tests.test_api tests.test_cli tests.test_deploy_config -v'
```

Expected: PASS.

- [ ] **Step 4: Back up SQLite**

Run:

```bash
ssh tencent-market 'cd /home/ubuntu/github/mono && cp data/market.sqlite3 data/market.sqlite3.$(date +%Y%m%d%H%M%S).bak && sqlite3 data/market.sqlite3 "PRAGMA integrity_check;"'
```

Expected: backup file created and `PRAGMA integrity_check` returns `ok`.

- [ ] **Step 5: Save SQLite row-count report**

Run:

```bash
ssh tencent-market 'cd /home/ubuntu/github/mono && PYTHONPATH=src .venv/bin/market sqlite-count-report --sqlite-db data/market.sqlite3 | tee logs/sqlite-count-report.$(date +%Y%m%d%H%M%S).csv'
```

Expected: report contains every online table and row count.

- [ ] **Step 6: Stop before cutover**

Do not set `MARKET_DATABASE_URL` or restart production into PostgreSQL until the user explicitly approves the production cutover.

---

## Final Verification

Run locally before requesting execution approval:

```bash
PYTHONPATH=src python3 -m unittest tests.test_db_schema tests.test_repositories tests.test_api tests.test_cli tests.test_settings tests.test_deploy_config -v
```

Expected: PASS.

When dependencies have been approved and installed, also run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_backfill tests.test_parquet_export tests.test_agent_data -v
```

Expected: PASS.

## Open Execution Gates

- Adding `psycopg[binary]` requires explicit user approval during Task 7.
- Adding `duckdb` and `pyarrow` requires explicit user approval during Task 10.
- Production PostgreSQL cutover requires explicit user approval during Task 13.
- Production PostgreSQL cutover is blocked if SQLite backup, SQLite integrity check, source row counts, or target row counts are missing.
