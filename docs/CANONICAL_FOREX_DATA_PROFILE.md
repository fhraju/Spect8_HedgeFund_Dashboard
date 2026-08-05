# Canonical Forex Data Profile

Authority: `IC_MARKETS_NY_CLOSE_FOREX_V1`
Backend contract: `backend/app/market_data/profiles/ic_markets_ny_close_forex_v1.py`

This profile freezes the provider-independent candle structure used by Spect8. It governs data construction only and does not change any approved strategy formula.

## Time and session contract

- Canonical storage uses timezone-aware UTC instants.
- Candle `open_time` is inclusive and `close_time` is exclusive.
- IC Markets Broker Time is a display/alignment wall clock, never a storage timezone.
- Broker wall time is New York wall time plus seven hours: UTC+2 while New York uses EST and UTC+3 while it uses EDT. IANA `America/New_York` rules are authoritative; annual transition dates are never hard-coded.
- The D1 session closes at 17:00 `America/New_York` and is labelled by that New York closing date.
- H1 opens on broker minute 00.
- H4 broker buckets are 00-04, 04-08, 08-12, 12-16, 16-20, and 20-00.

## Provider boundary

An adapter fetches raw values, maps symbols, declares `TimestampSemantics`, normalizes its provider timestamp once, and reports health. A raw candle carries provider name/symbol, canonical instrument, source timeframe, original provider timestamp, explicit UTC open/close, OHLCV, completion, source ID, receipt time, adapter version, and sanitized metadata.

`OPEN_TIME`, `CLOSE_TIME`, `INTERVAL_START`, and `INTERVAL_END` are explicit. `UNKNOWN` is rejected when an adapter supplies explicit UTC boundaries. Legacy replay fixtures may use the compatibility raw-string path, but provider certification does not accept that path.

Provider adapters must not create strategy-ready H4 or D1 candles. Native provider H4/D1 are comparison-only unless a future, separately versioned certification explicitly approves them.

## Canonical construction

H1 is the construction base. A canonical H1:

- is a completed, one-hour provider candle on a valid broker session boundary;
- preserves actual provider OHLC and optional volume;
- is never synthetic or forward-filled;
- retains source ID, adapter version, ingestion run, and raw evidence;
- is excluded when its broker opening weekday is Saturday or Sunday.

H4 requires exactly four contiguous valid H1 bars in one broker bucket. Open comes from the first H1, high/low are extrema, close comes from the fourth H1, and volume is summed only if every source volume is available. Empty weekend buckets are not created. An incomplete open-market bucket is quarantined and recorded as a quality issue.

D1 is reconstructed exclusively from H1 between consecutive New York 17:00 boundaries. It persists the session identifier, UTC boundaries, broker wall labels, all contributing H1 IDs, completeness/quality state, and profile version. DST-transition sessions use the actual number of elapsed UTC hours implied by consecutive local boundaries.

## Calendar, gaps, and failures

- `EXPECTED_MARKET_CLOSURE`: normal Friday 17:00 to Sunday 17:00 New York closure or an explicitly configured holiday/shortened closure. No candle is created and later evaluation may continue.
- `UNEXPECTED_DATA_GAP`: missing interval while the market is open. No synthetic candle is created; affected aggregates are quarantined, the issue is persisted, dependent evaluation is blocked, and provider health is unhealthy.
- `PROVIDER_PRICE_GAP`: adjacent real candles have discontinuous prices. Both prices are preserved without interpolation.
- Duplicate boundaries are rejected; forming bars are excluded; incomplete aggregates are quarantined.

Holiday and shortened-session intervals must be explicitly configured in the centralized `ForexMarketCalendar`. Absence of a configured closure never permits silent filling.

## Provenance invariants

Every canonical row records quality status, profile version, provider/adapter, source timeframe, source candle IDs, `synthetic`, `forward_filled`, expected-closure marker, ingestion run, and creation timestamp. D1 additionally records session identity and broker open/close labels.

Required provider-derived Forex invariants:

1. `synthetic = false`.
2. `forward_filled = false`.
3. No weekend H1 is stored.
4. Every H4/D1 traces to canonical H1 source IDs.
5. A Friday-to-Monday price difference is preserved.
6. Strategy evaluation receives only completed canonical H1 and H1-derived H4/D1.

## Reference fixture and certification

The sanitized frozen fixture and checksum live under `backend/tests/fixtures/ic_markets_forex_v1/`. It contains no account identifier, login, credential, server secret, or personal terminal path. Validate adapters with:

```powershell
.\.venv\Scripts\python.exe -m backend.app.market_data.certify_provider `
  --provider ic_markets_fixture `
  --instrument EUR/USD `
  --profile IC_MARKETS_NY_CLOSE_FOREX_V1 `
  --start 2026-07-30 `
  --end 2026-08-04
```

For Twelve Data, set its API key only in the ignored runtime environment and replace `ic_markets_fixture` with `twelve_data`. Certification compares structure, provenance, gaps, DST/session boundaries, and deterministic digests; it never requires cross-provider OHLC equality.
