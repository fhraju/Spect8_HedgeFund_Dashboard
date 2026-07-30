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

export type InstrumentStatus = {
  strategy_id: string;
  provider: string;
  instrument_id: string;
  timeframe: "H1" | "H4";
  source_case_id: string;
  synthetic: true;
  data_status: string;
  dashboard_state: string;
  filter_result: FilterResult;
  signal_result: SignalResult;
  levels_result: LevelsResult | null;
  levels_results: LevelsResult[];
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
  synthetic: true;
};

export type SyntheticEnvelope<T> = {
  synthetic: true;
  source: "PRODUCTION_ENGINE_WITH_SYNTHETIC_CANDLE_INPUTS";
  notice: string;
  data: T;
};

export type DashboardSnapshot = {
  health: SyntheticEnvelope<{
    status: string;
    mode: string;
    market_data: string;
    database: string;
  }>;
  statuses: SyntheticEnvelope<InstrumentStatus[]>;
  events: SyntheticEnvelope<EventRecord[]>;
};
