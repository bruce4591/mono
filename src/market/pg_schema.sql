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

CREATE TABLE IF NOT EXISTS bar_daily (
    instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id),
    trade_date DATE NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume_raw DOUBLE PRECISION,
    turnover_raw DOUBLE PRECISION,
    quote_currency TEXT NOT NULL,
    source TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (instrument_id, trade_date)
);

CREATE TABLE IF NOT EXISTS bar_intraday (
    instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id),
    interval TEXT NOT NULL,
    bar_start_ts_utc TIMESTAMPTZ NOT NULL,
    bar_end_ts_utc TIMESTAMPTZ NOT NULL,
    trade_date_local DATE NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume_raw DOUBLE PRECISION,
    turnover_raw DOUBLE PRECISION,
    is_closed_bar BOOLEAN NOT NULL DEFAULT FALSE,
    source TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (instrument_id, interval, bar_start_ts_utc)
);

CREATE TABLE IF NOT EXISTS market_snapshot (
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
    PRIMARY KEY (instrument_id, trade_date_local)
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

CREATE TABLE IF NOT EXISTS ranking_snapshot (
    board_name TEXT NOT NULL,
    snapshot_ts_utc TIMESTAMPTZ NOT NULL,
    rank INTEGER NOT NULL,
    instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id),
    turnover_raw DOUBLE PRECISION NOT NULL,
    quote_currency TEXT NOT NULL,
    change_pct DOUBLE PRECISION,
    source TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (board_name, snapshot_ts_utc, rank)
);

CREATE TABLE IF NOT EXISTS watchlist (
    watchlist_name TEXT NOT NULL,
    instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id),
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (watchlist_name, instrument_id)
);

CREATE TABLE IF NOT EXISTS job_state (
    job_name TEXT PRIMARY KEY,
    checkpoint TEXT,
    status TEXT NOT NULL,
    last_started_at TIMESTAMPTZ,
    last_finished_at TIMESTAMPTZ,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_health (
    source_name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    last_success_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
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

CREATE TABLE IF NOT EXISTS alert_rule (
    rule_id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    metric TEXT NOT NULL,
    operator TEXT NOT NULL,
    threshold DOUBLE PRECISION NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS alert_event (
    event_id BIGSERIAL PRIMARY KEY,
    rule_id BIGINT NOT NULL REFERENCES alert_rule(rule_id),
    instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id),
    triggered_at_utc TIMESTAMPTZ NOT NULL,
    metric TEXT NOT NULL,
    observed_value DOUBLE PRECISION NOT NULL,
    threshold DOUBLE PRECISION NOT NULL,
    message TEXT NOT NULL,
    is_acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS push_device (
    push_device_id BIGSERIAL PRIMARY KEY,
    push_token TEXT NOT NULL UNIQUE,
    getui_cid TEXT,
    platform TEXT NOT NULL,
    device_label TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at_utc TIMESTAMPTZ NOT NULL,
    updated_at_utc TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_push_device_getui_cid
    ON push_device (getui_cid)
    WHERE getui_cid IS NOT NULL;

CREATE TABLE IF NOT EXISTS mobile_alert_rule (
    mobile_alert_rule_id BIGSERIAL PRIMARY KEY,
    push_device_id BIGINT NOT NULL REFERENCES push_device(push_device_id),
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    condition_type TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'builtin',
    metric_key TEXT,
    operator TEXT,
    indicator_id BIGINT,
    created_by TEXT NOT NULL DEFAULT 'manual',
    threshold DOUBLE PRECISION NOT NULL,
    cooldown_seconds INTEGER NOT NULL DEFAULT 900,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at_utc TIMESTAMPTZ NOT NULL,
    updated_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS indicator_definition (
    indicator_id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    expression TEXT NOT NULL,
    input_scope TEXT NOT NULL,
    unit TEXT,
    created_by TEXT NOT NULL DEFAULT 'manual',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at_utc TIMESTAMPTZ NOT NULL,
    updated_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS indicator_value (
    indicator_value_id BIGSERIAL PRIMARY KEY,
    indicator_id BIGINT NOT NULL REFERENCES indicator_definition(indicator_id),
    instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id),
    value_ts_utc TIMESTAMPTZ NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    input_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL,
    UNIQUE (indicator_id, instrument_id, value_ts_utc)
);

CREATE TABLE IF NOT EXISTS mobile_alert_event (
    mobile_alert_event_id BIGSERIAL PRIMARY KEY,
    mobile_alert_rule_id BIGINT NOT NULL REFERENCES mobile_alert_rule(mobile_alert_rule_id),
    triggered_at_utc TIMESTAMPTZ NOT NULL,
    observed_value DOUBLE PRECISION NOT NULL,
    message TEXT NOT NULL,
    dedupe_key TEXT,
    alert_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    delivery_status TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_mobile_alert_event_rule_dedupe
    ON mobile_alert_event (mobile_alert_rule_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS mobile_alert_delivery (
    mobile_alert_delivery_id BIGSERIAL PRIMARY KEY,
    mobile_alert_event_id BIGINT NOT NULL REFERENCES mobile_alert_event(mobile_alert_event_id),
    push_device_id BIGINT NOT NULL REFERENCES push_device(push_device_id),
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    provider_message_id TEXT,
    last_error TEXT,
    created_at_utc TIMESTAMPTZ NOT NULL,
    updated_at_utc TIMESTAMPTZ NOT NULL,
    UNIQUE (mobile_alert_event_id, push_device_id, channel)
);

CREATE TABLE IF NOT EXISTS device_checkpoint (
    push_device_id BIGINT PRIMARY KEY REFERENCES push_device(push_device_id),
    last_seen_mobile_alert_event_id BIGINT NOT NULL DEFAULT 0,
    last_ack_mobile_alert_event_id BIGINT NOT NULL DEFAULT 0,
    updated_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS device_session (
    session_id TEXT PRIMARY KEY,
    push_device_id BIGINT NOT NULL REFERENCES push_device(push_device_id),
    transport TEXT NOT NULL,
    connected_at_utc TIMESTAMPTZ NOT NULL,
    last_seen_at_utc TIMESTAMPTZ NOT NULL,
    disconnected_at_utc TIMESTAMPTZ
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
