export type FilterMode = "MICRO" | "MACRO";

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

export type FilterAuditDailySession = {
  session_identifier: string;
  session_open_time: string;
  session_close_time: string;
  daily_high: string;
  daily_low: string;
};

export type FilterAuditBuyComparison = {
  recent_low: string;
  operator: "<=";
  buy_threshold: string;
  matched: boolean;
};

export type FilterAuditSellComparison = {
  recent_high: string;
  operator: ">=";
  sell_threshold: string;
  matched: boolean;
};

export type FilterAuditBar = {
  sequence: number;
  open_time: string;
  close_time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  source_id: string;
  recent_low: boolean;
  recent_high: boolean;
  expected_market_closure_before: boolean;
};

export type FilterAudit = {
  instrument_id: string;
  strategy_version: string;
  timeframe: "H1" | "H4";
  evaluation_time: string;
  evaluation_bar_open_time: string;
  evaluation_bar_close_time: string;
  evaluation_bar_open: string;
  evaluation_bar_high: string;
  evaluation_bar_low: string;
  evaluation_bar_close: string;
  evaluation_bar_confirmed_closed: boolean;
  completed_bar_count: number;
  available_completed_bar_count: number;
  lookback_period: number;
  lookback_start_time: string;
  lookback_end_time: string;
  recent_low: string;
  recent_low_bar_open_time: string;
  recent_low_bar_close_time: string;
  recent_high: string;
  recent_high_bar_open_time: string;
  recent_high_bar_close_time: string;
  daily_session: FilterAuditDailySession;
  daily_reference_sessions: FilterAuditDailySession[];
  atr_sessions: FilterAuditDailySession[];
  d1_context_eligibility_time: string;
  atr_period: number;
  atr_value: string;
  buffer_percentage: string;
  buffer_value: string;
  daily_low: string;
  daily_high: string;
  buy_threshold: string;
  sell_threshold: string;
  buy_comparison: FilterAuditBuyComparison;
  sell_comparison: FilterAuditSellComparison;
  final_classification: string;
  source_provider: string;
  construction_profile: string;
  canonical_timezone: string;
  display_timezone: string;
  daily_session_authority: string;
  selected_bars: FilterAuditBar[];
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
  filter_audit?: FilterAudit | null;
  strategy_version?: string;
  daily_filter_snapshot_id?: string | null;
  filter_mode?: FilterMode;
};

export type DailyFilterSnapshot = {
  snapshot_id: string;
  strategy_version: string;
  canonical_profile_version: string;
  provider: string;
  instrument: string;
  evaluation_time_utc: string;
  as_of_h1_close_time_utc: string;
  current_partial_d1: {
    session_identifier: string;
    session_open_utc: string;
    session_close_utc: string;
    first_h1_open_time_utc: string;
    last_h1_close_time_utc: string;
    h1_count: number;
    source_h1_ids: string[];
    source_checksum: string;
    open: string;
    high: string;
    low: string;
    close: string;
    quality_status: string;
  };
  previous_d1_candle_id: string;
  previous_d1_session_id: string;
  previous_d1_open_utc: string;
  previous_d1_close_utc: string;
  previous_d1_high: string;
  previous_d1_low: string;
  previous_d1_close: string;
  atr_period: number;
  atr_value: string;
  atr_source_d1_ids: string[];
  atr_source_checksum: string;
  buffer_percentage: string;
  buffer_value: string;
  buy_threshold: string;
  sell_threshold: string;
  buy_left_value: string;
  buy_operator: "<=";
  buy_right_value: string;
  buy_matched: boolean;
  sell_left_value: string;
  sell_operator: ">=";
  sell_right_value: string;
  sell_matched: boolean;
  final_classification: "NONE" | "BUY" | "SELL" | "BUY_AND_SELL";
  data_quality_status: string;
  ingestion_run_id: string | null;
  created_at: string;
};

export type WeeklyFilterSnapshot = {
  snapshot_id: string;
  filter_mode: "MACRO";
  strategy_version: string;
  canonical_profile_version: string;
  provider: string;
  instrument: string;
  evaluation_time_utc: string;
  as_of_h1_close_time_utc: string;
  current_partial_w1: {
    session_identifier: string;
    session_open_utc: string;
    session_close_utc: string;
    first_h1_open_time_utc: string;
    last_h1_close_time_utc: string;
    h1_count: number;
    source_h1_ids: string[];
    source_checksum: string;
    open: string;
    high: string;
    low: string;
    close: string;
    quality_status: string;
  };
  previous_w1_candle_id: string;
  previous_w1_session_id: string;
  previous_w1_open_utc: string;
  previous_w1_close_utc: string;
  previous_w1_open: string;
  previous_w1_high: string;
  previous_w1_low: string;
  previous_w1_close: string;
  atr_period: number;
  atr_value: string;
  atr_source_w1_ids: string[];
  atr_source_checksum: string;
  buffer_percentage: string;
  buffer_value: string;
  buy_threshold: string;
  sell_threshold: string;
  buy_left_value: string;
  buy_operator: "<=";
  buy_right_value: string;
  buy_matched: boolean;
  sell_left_value: string;
  sell_operator: ">=";
  sell_right_value: string;
  sell_matched: boolean;
  final_classification: "NONE" | "BUY" | "SELL" | "BUY_AND_SELL";
  data_quality_status: string;
  ingestion_run_id: string | null;
  created_at: string;
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
    display_symbol: string;
    display_name: string;
    asset_class: string;
    enabled: boolean;
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
  active_filter_mode?: FilterMode;
  filter_timeframe?: "D1" | "W1";
  daily_filter?: DailyFilterSnapshot | null;
  weekly_filter?: WeeklyFilterSnapshot | null;
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

export type ScannerTimeframe = {
  filter_status: string;
  signal_status: string;
  evaluation_timestamp: string | null;
  latest_filter_snapshot_id: string | null;
};

export type ScannerInstrument = {
  instrument_id: string;
  display_symbol: string;
  display_name: string;
  asset_class: string;
  enabled: boolean;
  polling_enabled?: boolean;
  provider_symbol: string;
  provider?: string;
  exchange?: string | null;
  mic_code?: string | null;
  provider_instrument_type?: string | null;
  provider_timezone?: string | null;
  validation_status?: string;
  instrument_kind?: "FOREX" | "SPOT_METAL" | "CRYPTO" | "ETF" | "DIRECT_MARKET";
  exposure_category?: string;
  underlying_description?: string | null;
  is_proxy?: boolean;
  proxy_for?: string | null;
  provider_exchange?: string | null;
  credit_budget_status?: string;
  provider_health: string;
  stale: boolean;
  data_status: string;
  latest_completed_h1_timestamp: string | null;
  latest_completed_h4_timestamp: string | null;
  current_filter: {
    status: string;
    as_of_h1_close_time: string | null;
    snapshot_id: string | null;
    source: "COMPLETED_H1" | "WAITING";
  };
  H1: ScannerTimeframe;
  H4: ScannerTimeframe;
  latest_error_summary: string | null;
  last_successful_provider_update: string | null;
};

export type ScannerSnapshot = {
  synthetic: boolean;
  source: "REPLAY_MARKET_DATA_PROVIDER" | "TWELVE_DATA_PROVIDER";
  notice: string;
  data: {
    generated_at: string;
    active_filter_mode?: FilterMode;
    filter_timeframe?: "D1" | "W1";
    instruments: ScannerInstrument[];
    credit_budget?: {
      state: string;
      window: string;
      daily_limit: number;
      operational_budget: number;
      reserve: number;
      estimated_credits_used: number;
      estimated_operational_remaining: number;
      estimated_total_remaining: number;
      reserve_preserved: boolean;
      request_count: number;
      provider_quota_limit?: number | null;
      provider_quota_used?: number | null;
      provider_quota_remaining?: number | null;
      provider_quota_window?: "MINUTE" | null;
    } | null;
  };
};

export type SyntheticEnvelope<T> = {
  synthetic: boolean;
  source: "REPLAY_MARKET_DATA_PROVIDER" | "TWELVE_DATA_PROVIDER";
  notice: string;
  data: T;
};

export type HistoricalReplayEnvelope<T> = {
  synthetic: false;
  source: "TWELVE_DATA_HISTORICAL_REPLAY";
  notice: string;
  data: T;
};

export type HistoricalReplayRun = {
  run_id: string;
  dataset_fingerprint: string | null;
  requested_dataset_fingerprint: string | null;
  provider: string;
  instrument: string;
  display_start: string;
  display_end: string;
  timeframes: Array<"H1" | "H4">;
  context_timeframe: "D1";
  strategy_version: string;
  status:
    | "PENDING"
    | "RUNNING"
    | "COMPLETED"
    | "PARTIAL"
    | "FAILED"
    | "QUARANTINED";
  progress: { total: number; completed: number; percent: number };
  duplicate_evaluations: number;
  quarantined_windows: number;
  determinism_digest: string | null;
  error: { code: string; detail: string } | null;
  orders: 0;
  fills: 0;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type HistoricalReplaySummary = {
  run: HistoricalReplayRun;
  dataset: {
    fingerprint: string;
    warmup_start: string;
    requested_ranges: Record<
      string,
      { start_utc: string; end_utc_exclusive: string }
    >;
    returned_ranges: Record<
      string,
      { first_close_utc: string | null; last_close_utc: string | null }
    >;
    candle_counts: Record<
      string,
      {
        received: number;
        accepted: number;
        duplicates: number;
        malformed: number;
        gaps: number;
        warmup: number;
        display: number;
      }
    >;
  } | null;
  evaluation_counts: {
    total: number;
    H1: number;
    H4: number;
    filter_pass: number;
    filter_fail: number;
    signal: number;
    no_signal: number;
  };
  reason_counts: Record<string, number>;
  event_count: number;
  data_quality: Array<{
    code: string;
    timeframe: string;
    start_utc: string | null;
    end_utc: string | null;
    detail: string;
  }>;
  execution: { enabled: false; orders: 0; fills: 0; detail: string };
};

export type HistoricalReplayEvaluation = {
  id: number;
  ordinal: number;
  signal_close_utc: string;
  replay_as_of_utc: string;
  timeframe: "H1" | "H4";
  filter_outcome: "PASS" | "FAIL";
  signal_outcome: "SIGNAL" | "NO_SIGNAL";
  dashboard_state: string;
  d1_context_close_utc: string;
  reason_codes: string[];
  market_values: MarketValues;
};

export type HistoricalReplayEvaluationPage = {
  items: HistoricalReplayEvaluation[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
};

export type HistoricalReplayEvaluationDetail = HistoricalReplayEvaluation & {
  status: Record<string, unknown>;
  evaluation: Record<string, unknown>;
  input: {
    replay_as_of_utc: string;
    signal_bars: Array<Record<string, unknown>>;
    daily_bars: Array<Record<string, unknown>>;
  };
  events: Array<{
    sequence: number;
    event_type: string;
    occurred_at: string;
    payload: Record<string, unknown>;
  }>;
};
