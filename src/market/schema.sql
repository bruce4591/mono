CREATE TABLE IF NOT EXISTS instrument (
    instrument_id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    display_name TEXT NOT NULL,
    exchange TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    quote_currency TEXT NOT NULL,
    timezone TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    extra_meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (market, symbol)
);

CREATE TABLE IF NOT EXISTS bar_daily (
    instrument_id INTEGER NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume_raw REAL,
    turnover_raw REAL,
    quote_currency TEXT NOT NULL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (instrument_id, trade_date),
    FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id)
);

CREATE TABLE IF NOT EXISTS bar_intraday (
    instrument_id INTEGER NOT NULL,
    interval TEXT NOT NULL,
    bar_start_ts_utc TEXT NOT NULL,
    bar_end_ts_utc TEXT NOT NULL,
    trade_date_local TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume_raw REAL,
    turnover_raw REAL,
    is_closed_bar INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (instrument_id, interval, bar_start_ts_utc),
    FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id)
);

CREATE TABLE IF NOT EXISTS market_snapshot (
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
    PRIMARY KEY (instrument_id, trade_date_local),
    FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id)
);

CREATE TABLE IF NOT EXISTS ranking_snapshot (
    board_name TEXT NOT NULL,
    snapshot_ts_utc TEXT NOT NULL,
    rank INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    turnover_raw REAL NOT NULL,
    quote_currency TEXT NOT NULL,
    change_pct REAL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (board_name, snapshot_ts_utc, rank),
    FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id)
);

CREATE TABLE IF NOT EXISTS watchlist (
    watchlist_name TEXT NOT NULL,
    instrument_id INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (watchlist_name, instrument_id),
    FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id)
);

CREATE TABLE IF NOT EXISTS job_state (
    job_name TEXT PRIMARY KEY,
    checkpoint TEXT,
    status TEXT NOT NULL,
    last_started_at TEXT,
    last_finished_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_health (
    source_name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    last_success_at TEXT,
    last_error_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bar_daily_trade_date
    ON bar_daily (trade_date);

CREATE INDEX IF NOT EXISTS idx_bar_intraday_lookup
    ON bar_intraday (instrument_id, interval, bar_start_ts_utc);

CREATE INDEX IF NOT EXISTS idx_market_snapshot_turnover
    ON market_snapshot (trade_date_local, turnover_raw DESC);

