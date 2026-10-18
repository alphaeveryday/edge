# Live domestic distribution comparison

Captured 2026-10-18. `kis_069500.json` preserves the public KIS domestic
distribution response and non-secret query parameters. No authentication headers
are stored. `kodex_069500_comparison.json` contains the seven corresponding
issuer events parsed from the KODEX distribution endpoint
`https://www.samsungfund.com/api/v1/kodex/distribution.do` (ticker 069500,
all-period pagination). The issuer comparison is normalized evidence, not raw
issuer response bytes. All seven record dates, per-unit cash amounts and payment
dates matched. This sample does not establish completeness for every ETF.
