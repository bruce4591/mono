export type MobileBoardItem = {
  market: string;
  symbol: string;
  name: string;
  last_price: number | null;
  change_pct: number | null;
  turnover: number | null;
  volume: number | null;
  rank: number | null;
  rank_change: number | null;
  data_time: string | null;
};

export type MobileBoard = {
  key: string;
  title: string;
  market: string;
  data_time: string | null;
  items: MobileBoardItem[];
};

export type MobileHomePayload = {
  server_time: string;
  boards: MobileBoard[];
};

export type MobileBar = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  turnover: number | null;
};

export type MobileInstrumentDetailPayload = {
  instrument: {
    market: string;
    symbol: string;
    name: string;
    asset_class: string | null;
  };
  snapshot: {
    last_price: number | null;
    change_pct: number | null;
    volume: number | null;
    turnover: number | null;
    data_time: string | null;
    source: string | null;
  };
  periods: string[];
  bars: MobileBar[];
};

export type MobileAlertEvent = {
  mobile_alert_event_id: number;
  market: string;
  symbol: string;
  title: string;
  body: string;
  triggered_at_utc: string;
  observed_value: number;
  threshold: number;
  delivery_status: string;
  data?: Record<string, unknown>;
};

export type MobileAlertEventsPayload = {
  events: MobileAlertEvent[];
};

export type MobileAlertRule = {
  mobile_alert_rule_id: number;
  push_device_id: number;
  enabled: boolean;
  market: string;
  symbol: string;
  condition_type: string;
  source_type: string;
  metric_key: string | null;
  operator: string | null;
  indicator_id: number | null;
  created_by: string;
  threshold: number;
  cooldown_seconds: number | null;
  created_at_utc: string;
  updated_at_utc: string;
};

export type MobileAlertRulesPayload = {
  rules: MobileAlertRule[];
};
