# Investment Lab

Local daily ETF/cash investment experiment; IC excluded. Ticket ALPHA-1308.

## Boundaries

Trusted controller owns immutable experiment configuration, market settlement tape,
AWS reader credentials, PIT gate and SQLite journal. The model gets only its daily
context and registered search/read tools. Neither filesystem/shell nor direct
network tools are exposed. The human dashboard can inspect audit records but the
model cannot query it. Prompt instructions are not permission enforcement.

Source candidate: analysis-engine-v2 agent runner. Current deployment workflow still
targets analysis-engine (v1); v2 is the latest local agent/tool harness, not verified
as the deployed service. Copy only agent loop and schema shaping, excluding writers,
credentials, fixture generators, service configuration and deployment files.

## Execution contract

Initial cash KRW 100,000,000; ticker 305720; no shorts/borrowing. Explicit full KRX
calendar and unadjusted opening/closing prices are required. No inferred weekdays.
18:00 KST decision executes next listed trading day's open. HOLD preserves units.
Target uses pre-trade opening NAV, whole shares, buy affordability including costs.
Cash interest and risk-free rate default zero. Cost bps are mandatory assumptions.
Accrue distributions from pre-ex-date holdings, pay on payment date; receivables
count in NAV but not spendable cash. Unknown distribution coverage blocks real runs.
Suspensions cancel that day's pending execution; missing prices fail the run.
Final-day decision is explicitly unexecuted; no forced liquidation.

Atomic day transactions persist decision, admitted inputs, gate audit, fills, cash,
shares, receivables and NAV. Resume checks input/config/code hashes and starts after
the last committed day; a failed model call cannot create a fill twice.

Turnover: sum of absolute executed notionals / previous day's NAV (buys + sells,
not divided by two). Sharpe: sample standard deviation, sqrt(252), annual RF/252.
CAGR uses actual calendar days / 365.25. MDD includes initial cash NAV. Undefined
Sharpe is null with reason. Fee-free comparison is a separately recomputed ledger.

## PIT

Every component requires publication, availability, content-version and archive
capture timestamps <= cutoff, immutable evidence reference and matching hash.
Metadata is supplied only by a trusted adapter, never by the model. Absence or
conflict fails closed. This conservative capture requirement can overblock genuine
old documents captured later. Current web discovery alone does not prove historical
content. The tool searches an imported historical web archive; live-web provider
integration remains disabled until trustworthy version evidence is available.
Model training memory and historical search-ranking reproduction are not solved.

## Work units / checkpoints

1. Discovery: separate worktree, Jira created, two reader AWS roles authenticated.
   Existing v2 source calendar supports only 2026. User worktree unchanged.
2. Accounting: 8 intent tests passed; further replay/distribution tests required.
3. PIT: negative tests written before implementation; benchmark is synthetic
   adversarial contract coverage, not a measured claim about the live web.
4. Cloud: inspect fixed session document and effective DB grants before import.
5. Dashboard: local-only journal views and new experiment instructions.
6. Verification: report local tests, live access, PIT coverage and two-year run
   separately. Do not fabricate data or performance to make an empty chart full.

Each work unit targets <=4,000 authored/reasoning tokens; summaries recorded here.

Final checkpoint: 30 tests passed, wheel/sdist built, JavaScript syntax checked.
Live SSM/TLS reader query succeeded via isolated loopback port15435. Price export
remains quarantined; no two-year model replay or live-web-provider test performed.
Independent review found and closed both settlement-price and generic-price-read
PIT bypasses. UI has no-data and synthetic labels. Browser visual QA unavailable.
Operational code and the user's original worktree edit are unchanged.
