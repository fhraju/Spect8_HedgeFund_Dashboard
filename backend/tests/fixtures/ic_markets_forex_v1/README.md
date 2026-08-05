# Frozen IC Markets Forex V1 reference

This sanitized fixture was extracted from the read-only IC Markets Global MT5 `EURUSD` H1 export produced on 2026-08-05. It covers a full Friday session, the 48-hour weekend closure, and a full Monday session. Canonical timestamps are UTC; the corresponding broker clock follows `America/New_York` wall time plus seven hours.

The export authority was IC Markets Global MT5 build 5833. Account number, login, credentials, server secrets, and the personal terminal path were omitted. Provider prices and tick volumes remain unchanged.

Verify before use:

```powershell
(Get-FileHash reference.json -Algorithm SHA256).Hash.ToLower()
```

Approved SHA-256: `e5ff9efbb98aaaf64840d717bca40e7aa41c8870d6bc039e51cfee5905a67699`.

Any intentional fixture change requires a new schema/profile version, documented re-export evidence, and a separately reviewed checksum update. Never silently replace this file.
