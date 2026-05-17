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
  unit?: string | null;
  metric_label?: string | null;
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

export type MobileAlertMarker = {
  mobile_alert_event_id: number;
  time: string;
  price: number;
  direction: "up" | "down" | string;
  label: string;
  condition_type: string;
  condition_label: string;
  ma11: number | null;
  volume_ratio: number | null;
  triggered_at_utc: string;
  body: string;
};

export type MobileInstrumentDetailPayload = {
  instrument: {
    market: string;
    symbol: string;
    name: string;
    asset_class: string | null;
    price_tick_size: string | null;
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
  alert_markers: MobileAlertMarker[];
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

export type MobileStrategyTrade = {
  action: string;
  price: number | null;
  event_time_utc: string;
  realized_return_pct: number | null;
};

export type MobileStrategySymbol = {
  market: string;
  symbol: string;
  position_status: string;
  side: string | null;
  entry_price: number | null;
  exit_price: number | null;
  current_price: number | null;
  opened_at_utc: string | null;
  closed_at_utc: string | null;
  realized_return_pct: number | null;
  unrealized_return_pct: number | null;
  last_trade: MobileStrategyTrade | null;
};

export type MobileStrategy = {
  strategy_id: string;
  name: string;
  description: string;
  execution_mode: string;
  enabled: boolean;
  updated_at_utc: string;
  symbols: MobileStrategySymbol[];
};

export type MobileStrategiesPayload = {
  strategies: MobileStrategy[];
};
