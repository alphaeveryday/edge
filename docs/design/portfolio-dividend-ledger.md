# Portfolio cash distributions

## Approved architecture and current implementation

Public source data uses the existing data-pipeline raw ? normalization/quality ?
canonical ? database loader contract. Customer holdings and derived cash stay in
the portfolio service. Cash is summed without reinvestment. No paid vendor or new
always-on service is required.

The first implementation unit provides domestic KIS collection, immutable source
observations, a latest-revision database view and a CashDistribution object/view.
It is callable through the pipeline CLI. Scheduled collection, US issuer ingestion,
portfolio read integration and customer-report deployment are subsequent units.
The earlier portfolio-worker-only ingestion code is an unreviewed, undeployed
prototype and is not the public-data target architecture.

## Source verification on 2026-10-18

Six domestic and ten overseas KIS API calls succeeded for 2025-01-01 through
2026-10-18. Success means the API responded, not complete dividend coverage.

| API / symbols | Single-page response counts |
|---|---|
| Domestic: 005930 / 069500 / 360750 / 379800 / 458730 / 0093A0 | 7 / 7 / 7 / 6 / 21 / 2 |
| Overseas amounts: VOO / IVV / SCHD / JEPI / NVDL | 21 / 30 / 7 / 30 / 0 |
| Overseas ICE rights: VOO / IVV / SCHD / JEPI / NVDL | 7 / 7 / 8 / 23 / 42 |

KODEX 200's seven events matched the issuer's amount and payment date exactly.
Fixtures and their provenance are in data-pipeline/tests/fixtures/dividends.
A subsequent live run through the implemented collector and normalizer produced
43 unique observations for five domestic ETFs, with both stages exiting 0.
No production distribution table was written by these probes.

Observed hazards and decisions:

- VOO requests include VOOG/VOOV; IVV requests include IVVB/IVVW. JEPI includes
  US46641Q3323 and IE000U5MJOZ6 with different product-type codes. Ticker-only
  filtering is insufficient for overseas amounts.
- IVV's 2025-03-18 KIS amount (0.03644) differs from the issuer's income and total
  distribution (1.764574). This discrepancy persisted in a narrower March query.
  Its cause is unresolved. Do not promote this KIS amount as the issuer total.
- ICE contains partial dates such as 20250300. Never invent a day or a paid receipt.
- A domestic zero with no payment date is a pending schedule, not confirmed zero.
- KIS dividend-rate fields have an unverified basis. Derive trailing yield from
  verified cash distributions and price; never reuse that field blindly.
- Continuation M/F causes date-window splitting; an incomplete single-day request
  remains a visible error. A partial response is not accepted as complete history.
- Source failures, malformed rows and conflicting same-event amounts remain
  quality failures. Good observations are retained independently; failures never
  replace prior data with zero.

US remains issuer-first ? verified KIS supplements ? yfinance fallback. US raw
probe results have not been enabled as a production amount collector.

## Identity, rows and object mapping

A production read-only lookup found 069500 in the instrument master but not the
queried 360750/VOO/IVV/SCHD/JEPI. Requiring all master IDs before collection would
recreate the missing-data blockage. Follow the existing source-observation pattern:

- One source observation revision = market_code + ticker + source_vendor +
  source_event_key + raw_run_id. Codes remain strings. No synthetic instrument ID.
- Domestic event key retains record date, vendor dividend kind and share kind.
  Conflicting values for this key in one run are rejected rather than summed.
- SQL latest selection uses available_at and raw_run_id before any publication
  filter. A cancellation or pending correction must hide the older published value.
- CashDistribution identity is the JSON array of market, ticker, vendor and event
  key. Its view resolves instrument_id through the existing unique
  (market_code, ticker) constraint. Missing masters produce a null reference.
- ForSecurity links use actual ETF/equity profile rows; an instrument without its
  target profile does not produce a link to a nonexistent object.
- Source mapping is a view join, not a new physical FK. Object definitions are
  experimental; local verification does not claim operational OMS deployment.

The observation table stores original-currency gross Decimal cash per share,
record/ex/payment dates separately, confirmation, receipt/availability time,
raw object key/hash and canonical run/artifact provenance. It holds no customer
identity, account, quantity, tax assumption or investment advice.

## Remaining customer calculation contract

Published payments supersede the corresponding forecast; never sum provider
copies of the same event. Use entitlement-date holding history for past cash,
not today's quantity. Registration does not establish prior ownership. Keep
special distributions and splits distinct; split-adjusted history cannot simply
be multiplied by historical unadjusted shares. Unknown tax/FX does not erase gross
original-currency cash. Do not mutate customer cash balances or call modeled
payments bank-confirmed receipts.

Display: trailing 12-month cash yield, next-year forecasts, and disclosed portfolio
cash are separate calculations. Annual forecasts sum cash without reinvestment.
The mobile holdings list/currency toggle is a separate UI work unit.
