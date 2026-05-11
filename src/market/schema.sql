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

CREATE TABLE IF NOT EXISTS board_refresh_state (
    board_name TEXT PRIMARY KEY,
    last_requested_at_utc TEXT,
    last_started_at_utc TEXT,
    last_finished_at_utc TEXT,
    status TEXT NOT NULL DEFAULT 'idle',
    last_error TEXT,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_rule (
    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    metric TEXT NOT NULL,
    operator TEXT NOT NULL,
    threshold REAL NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS alert_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    triggered_at_utc TEXT NOT NULL,
    metric TEXT NOT NULL,
    observed_value REAL NOT NULL,
    threshold REAL NOT NULL,
    message TEXT NOT NULL,
    is_acknowledged INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (rule_id) REFERENCES alert_rule(rule_id),
    FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id)
);

CREATE TABLE IF NOT EXISTS push_device (
    push_device_id INTEGER PRIMARY KEY AUTOINCREMENT,
    push_token TEXT NOT NULL UNIQUE,
    getui_cid TEXT,
    platform TEXT NOT NULL,
    device_label TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mobile_alert_rule (
    mobile_alert_rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    push_device_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    condition_type TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'builtin',
    metric_key TEXT,
    operator TEXT,
    indicator_id INTEGER,
    created_by TEXT NOT NULL DEFAULT 'manual',
    threshold REAL NOT NULL,
    cooldown_seconds INTEGER NOT NULL DEFAULT 900,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY (push_device_id) REFERENCES push_device(push_device_id)
);

CREATE TABLE IF NOT EXISTS indicator_definition (
    indicator_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    expression TEXT NOT NULL,
    input_scope TEXT NOT NULL,
    unit TEXT,
    created_by TEXT NOT NULL DEFAULT 'manual',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS indicator_value (
    indicator_value_id INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    value_ts_utc TEXT NOT NULL,
    value REAL NOT NULL,
    input_snapshot TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY (indicator_id) REFERENCES indicator_definition(indicator_id),
    FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id),
    UNIQUE (indicator_id, instrument_id, value_ts_utc)
);

CREATE TABLE IF NOT EXISTS mobile_alert_event (
    mobile_alert_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mobile_alert_rule_id INTEGER NOT NULL,
    triggered_at_utc TEXT NOT NULL,
    observed_value REAL NOT NULL,
    message TEXT NOT NULL,
    dedupe_key TEXT,
    delivery_status TEXT NOT NULL,
    FOREIGN KEY (mobile_alert_rule_id) REFERENCES mobile_alert_rule(mobile_alert_rule_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_mobile_alert_event_rule_dedupe
    ON mobile_alert_event (mobile_alert_rule_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS mobile_alert_delivery (
    mobile_alert_delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mobile_alert_event_id INTEGER NOT NULL,
    push_device_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    provider_message_id TEXT,
    last_error TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY (mobile_alert_event_id) REFERENCES mobile_alert_event(mobile_alert_event_id),
    FOREIGN KEY (push_device_id) REFERENCES push_device(push_device_id),
    UNIQUE (mobile_alert_event_id, push_device_id, channel)
);

CREATE TABLE IF NOT EXISTS device_checkpoint (
    push_device_id INTEGER PRIMARY KEY,
    last_seen_mobile_alert_event_id INTEGER NOT NULL DEFAULT 0,
    last_ack_mobile_alert_event_id INTEGER NOT NULL DEFAULT 0,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY (push_device_id) REFERENCES push_device(push_device_id)
);

CREATE TABLE IF NOT EXISTS device_session (
    session_id TEXT PRIMARY KEY,
    push_device_id INTEGER NOT NULL,
    transport TEXT NOT NULL,
    connected_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL,
    disconnected_at_utc TEXT,
    FOREIGN KEY (push_device_id) REFERENCES push_device(push_device_id)
);

CREATE INDEX IF NOT EXISTS idx_bar_daily_trade_date
    ON bar_daily (trade_date);

CREATE INDEX IF NOT EXISTS idx_bar_intraday_lookup
    ON bar_intraday (instrument_id, interval, bar_start_ts_utc);

CREATE INDEX IF NOT EXISTS idx_market_snapshot_turnover
    ON market_snapshot (trade_date_local, turnover_raw DESC);

CREATE INDEX IF NOT EXISTS idx_latest_market_snapshot_turnover
    ON latest_market_snapshot (trade_date_local, turnover_raw DESC);

CREATE INDEX IF NOT EXISTS idx_market_snapshot_history_lookup
    ON market_snapshot_history (instrument_id, snapshot_ts_utc DESC);


CREATE INDEX IF NOT EXISTS idx_alert_event_triggered
    ON alert_event (triggered_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_mobile_alert_delivery_status
    ON mobile_alert_delivery (status, updated_at_utc);

CREATE INDEX IF NOT EXISTS idx_device_session_push_device
    ON device_session (push_device_id, disconnected_at_utc, last_seen_at_utc);
