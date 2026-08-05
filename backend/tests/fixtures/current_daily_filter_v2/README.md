# Frozen Current Daily Filter V2 reference

This new fixture freezes the corrected `MICRO_DAILY_FILTER_CURRENT_D1_V2`
formula without changing any previous golden or IC Markets fixture. Its session
structure is a sanitized deterministic derivative of the approved IC Markets
Forex V1 reference; no account, login, server credential, or terminal path is
present.

It covers exact Decimal calculations for BUY, SELL, BUY_AND_SELL, NONE,
inclusive equality, a completed-H1 partial session, a matching H4 completion,
weekend closure and price-gap preservation, New York DST offsets, and an
unexpected missing-H1 quarantine.

Any change requires a new fixture version and reviewed checksum.

Approved SHA-256: `c68fedcd8adae0061c45c6b0dbbcf9c62caff7d5ab4d87230d688efc53804afd`.
