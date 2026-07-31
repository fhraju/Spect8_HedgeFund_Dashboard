export type FilterResult = {
  buy_matched: boolean;
  sell_matched: boolean;
  daily_buy_level: number;
  daily_sell_level: number;
};

export type SignalResult = {
  technical_buy: boolean;
  technical_sell: boolean;
  confirmed_buy: boolean;
  confirmed_sell: boolean;
};

export type LevelsResult = {
  direction: "BUY" | "SELL";
  entry_reference: number;
  raw_stop: number;
  display_stop: number;
  target: number;
  target_risk_usd: number;
  contract_size: number | null;
  contract_status:
    | "VALID"
    | "BELOW_PROVIDER_MINIMUM"
    | "METADATA_UNAVAILABLE";
};

export type MarketValues = {
  signal_open: number;
  signal_high: number;
  signal_low: number;
  signal_close: number;
  sma10: number;
  sma20: number;
  atr_d1_wilder_5: number;
  daily_raw_low: number;
  daily_raw_high: number;
  daily_buy_level: number;
  daily_sell_level: number;
  recent_low_21: number;
  recent_high_21: number;
  daily_context_close_time: string | null;
};

export type InstrumentStatus = {
  strategy_id: string;
  provider: string;
  instrument_id: string;
  timeframe: "H1" | "H4";
  source_case_id: string;
  synthetic: boolean;
  data_status: string;
  dashboard_state: string;
  filter_result: FilterResult;
  signal_result: SignalResult;
  levels_result: LevelsResult | null;
  levels_results: LevelsResult[];
  reason_codes: string[];
  market_values: MarketValues | null;
  signal_bar_close_time: string;
  last_update: string;
  idempotency_key: string;
};

export type EventRecord = {
  id: number;
  idempotency_key: string;
  sequence: number;
  event_type: string;
  occurred_at: string;
  instrument_id: string;
  timeframe: "H1" | "H4";
  source_case_id: string;
  payload: Record<string, unknown>;
  synthetic: boolean;
};

export type ProviderHealth = {
  provider: string;
  state: string;
  previous_state: string | null;
  checked_at: string;
  latest_completed_close: string | null;
  freshness_seconds: number | null;
  detail: string;
  synthetic: boolean;
};

export type ProviderSync = {
  provider: string;
  state: string;
  last_attempt_at: string;
  last_success_at: string | null;
  detail: string;
};

export type DashboardData = {
  generated_at: string;
  data_state: string;
  stale: boolean;
  provider_health: ProviderHealth | null;
  provider_sync: ProviderSync | null;
  instrument: {
    instrument_id: string;
    provider: string;
    provider_symbol: string;
    display_name: string;
    asset_class: string;
    session_timezone: string;
    timeframes: string[];
    price_precision: number;
    synthetic: boolean;
  };
  latest_candles: {
    H1: string | null;
    H4: string | null;
    D1: string | null;
  };
  evaluations: InstrumentStatus[];
  recent_events: EventRecord[];
  execution: {
    enabled: false;
    orders: 0;
    fills: 0;
    detail: string;
  };
};

export type DashboardSnapshot = {
  synthetic: boolean;
  source: "REPLAY_MARKET_DATA_PROVIDER" | "TWELVE_DATA_PROVIDER";
  notice: string;
  data: DashboardData;
};

export type SyntheticEnvelope<T> = {
  synthetic: boolean;
  source: "REPLAY_MARKET_DATA_PROVIDER" | "TWELVE_DATA_PROVIDER";
  notice: string;
  data: T;
};
