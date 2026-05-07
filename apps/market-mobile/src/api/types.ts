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
  title: string;
  body: string;
  data?: Record<string, unknown>;
};

export type MobileAlertEventsPayload = {
  events: MobileAlertEvent[];
};

export type MobileAlertRule = {
  mobile_alert_rule_id: number;
  name: string;
  enabled: boolean;
  market: string | null;
  symbol: string | null;
  metric: string;
  operator: string;
  threshold: number | null;
  cooldown_seconds: number | null;
  last_triggered_at: string | null;
};

export type MobileAlertRulesPayload = {
  rules: MobileAlertRule[];
};
