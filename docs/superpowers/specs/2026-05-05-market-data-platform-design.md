# Market Data Platform Design

## Goal

Move the market data layer from the current SQLite-first MVP into a platform
that can support real-time app reads, high-volume alert evaluation, auditability,
and AI Agent access to both current and historical market data.

The target architecture uses PostgreSQL as the online operational database and
Parquet queried through DuckDB as the historical analytics layer.

## Current Baseline

The current schema stores instruments, daily bars, intraday bars, latest market
snapshots, ranking snapshots, watchlists, alert rules, mobile alert events,
delivery attempts, devices, sessions, custom indicator definitions, and custom
indicator values in SQLite.

This is sufficient for the current MVP because writes are modest and most API
reads target the latest board or latest instrument snapshot. It is not sufficient
as the long-term shape because `market_snapshot` stores one row per instrument
per local trade date. That makes it useful for latest daily display, but not for
intraday snapshot history, alert trigger reconstruction, indicator backtesting,
or Agent analysis.

## Decision

Use a two-layer data architecture:

- PostgreSQL for online business state and recent market data.
- Parquet files queried by DuckDB for historical market data and analytical
  Agent workloads.

Do not use DuckDB or Parquet as the primary online store. They are excellent for
local analytics and large historical scans, but they are not the right source of
truth for app sessions, device registration, alert delivery state, rule edits,
or concurrent online writes.

## Online PostgreSQL Layer

PostgreSQL owns all mutable online state:

- instruments and watchlists
- latest market snapshots
- recent intraday bars needed by the app and alert workers
- ranking snapshots used by API boards
- alert rules, alert events, delivery attempts, checkpoints, and sessions
- custom indicator definitions and recent indicator values
- Agent-created rules and audit records
- job state, source health, and board refresh state

The first schema upgrade should split current snapshots into two concepts:

- `latest_market_snapshot`: one latest row per instrument.
- `market_snapshot_history`: append-style snapshot history keyed by
  `(instrument_id, snapshot_ts_utc)`.

This keeps app reads fast while preserving enough history to explain alert
triggers and feed indicators.

## Historical Parquet Layer

Parquet owns large historical datasets:

- long-range daily bars
- long-range intraday bars
- historical snapshots if retained beyond the PostgreSQL hot window
- computed factor and indicator history
- Agent research datasets

Recommended layout:

```text
data/
  market=HK/
    asset_class=stock/
      symbol=00700/
        interval=1d/
          year=2026/
            part-000.parquet
        interval=1m/
          year=2026/
            month=05/
              part-000.parquet
  market=US/
    asset_class=stock/
      symbol=AAPL/
        interval=1d/
          year=2026/
            part-000.parquet
        interval=1m/
          year=2026/
            month=05/
              part-000.parquet
```

The layout partitions by market, asset class, symbol, interval, and time. It
keeps symbol-level isolation without forcing one ever-growing file per symbol.
Writers can append or compact partitions without rewriting the entire dataset.

## Data Flow

```text
collector
  -> normalize instrument and quote data
  -> write latest and recent data to PostgreSQL
  -> append historical partitions to Parquet
  -> update job_state and source_health

API
  -> read current app data from PostgreSQL

alert worker
  -> evaluate against PostgreSQL latest/recent data
  -> write mobile_alert_event and mobile_alert_delivery rows to PostgreSQL

AI Agent tools
  -> read current state, rules, and recent data from PostgreSQL
  -> read long history from DuckDB over Parquet
  -> create proposed rules or indicators through audited PostgreSQL APIs
```

## Agent Access Model

Agents should not write directly into alert tables. They should call a small
server-side API that validates and audits every proposed change.

Add an audit model before enabling broad Agent write access:

- `agent_action_log`: actor, action type, request payload, result payload,
  approval state, created timestamp.
- `alert_rule_version`: immutable snapshots of alert rule changes.
- `indicator_definition_version`: immutable snapshots of custom indicator
  definition changes.

Agents may read from PostgreSQL and DuckDB, but writes that affect notifications
must go through validated application services.

## Migration Strategy

Phase 1 migrates online state from SQLite to PostgreSQL while preserving current
behavior:

1. Add a versioned migration system.
2. Create PostgreSQL schema matching the current SQLite tables.
3. Split `market_snapshot` into `latest_market_snapshot` and
   `market_snapshot_history`.
4. Add `board_refresh_state` for durable one-minute refresh throttling.
5. Port API, sync jobs, alert workers, and tests to a database adapter that can
   run against PostgreSQL.
6. Backfill PostgreSQL from the current SQLite database.
7. Deploy PostgreSQL behind the existing API.

Phase 2 adds Parquet exports:

1. Export daily bars and intraday bars to partitioned Parquet.
2. Add a DuckDB read helper for Agent and offline jobs.
3. Add retention policy for PostgreSQL hot data.
4. Add compaction jobs for small Parquet files.

Phase 3 adds Agent-safe data access:

1. Add audited Agent APIs for rule and indicator proposals.
2. Add read-only Agent tools for PostgreSQL latest state.
3. Add read-only Agent tools for DuckDB historical scans.
4. Add approval workflow for notification-affecting writes.

## Error Handling

PostgreSQL remains the source of truth for online state. If Parquet export
fails, app reads and alerts should continue from PostgreSQL, and the export job
should update `job_state` and `source_health`.

If PostgreSQL writes fail, collectors and alert workers should fail visibly and
not silently write only to Parquet. Parquet is not allowed to become the only
copy of online state.

If DuckDB queries fail, Agent historical analysis should report a data-source
error without affecting app APIs or alert delivery.

## Testing

Add tests for:

- migration creates PostgreSQL tables and indexes
- SQLite-to-PostgreSQL backfill preserves instruments, snapshots, rules, and
  delivery state
- latest snapshot upserts replace the current latest row
- snapshot history appends by timestamp
- board API reads from latest snapshots after refresh
- alert evaluation reads from PostgreSQL latest and recent data
- Parquet export writes expected partition paths
- DuckDB helper can query a symbol/time range from Parquet
- Agent write APIs create audit records before changing rules or indicators

## Operational Notes

Start with PostgreSQL on the existing server for simplicity. Enable regular
backups before moving production writes. Keep SQLite export/backfill scripts
until the PostgreSQL cutover has been verified.

Use PostgreSQL indexes for API and alert paths first. Add Parquet only after the
online migration is stable, because Parquet solves historical scale, not app
correctness.
